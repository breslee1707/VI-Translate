import abc
import ast
import logging
import os
import sys
import threading

import cv2
import numpy as np
from babeldoc.assets.assets import get_doclayout_onnx_model_path

try:
    import onnxruntime
except ImportError as e:
    if "DLL load failed" in str(e):
        raise OSError(
            "Microsoft Visual C++ Redistributable is not installed. "
            "Download it at https://aka.ms/vs/17/release/vc_redist.x64.exe"
        ) from e
    raise

logger = logging.getLogger(__name__)

CPU_PROVIDER = "CPUExecutionProvider"
_BACKEND_PROVIDERS = {
    "cpu": [CPU_PROVIDER],
    "coreml": ["CoreMLExecutionProvider", CPU_PROVIDER],
    "cuda": ["CUDAExecutionProvider", CPU_PROVIDER],
    "dml": ["DmlExecutionProvider", CPU_PROVIDER],
}
# Listed by every onnxruntime wheel, but it only serves remote endpoints and
# never runs a node of this model, so it is not an accelerator to fall back from.
_PASSIVE_PROVIDERS = frozenset({CPU_PROVIDER, "AzureExecutionProvider"})
BACKEND_VARIABLE = "PDF_TRANSLATE_ONNX_BACKEND"

_preferred_backend: str | None = None


class LayoutModelError(RuntimeError):
    """The layout model could not run on this machine, even on the CPU."""


def preferred_providers(name: str | None = None) -> list[str]:
    """Execution providers for the layout model: the CPU unless asked otherwise.

    Taking every available provider let onnxruntime pick CoreML on Apple
    Silicon, which on macOS 26 and later builds the session and then refuses to
    run it ("Error executing model: Error in building plan"), so no document
    translated at all on those Macs. BabelDOC runs this same model on the CPU
    only, and one page of layout inference is a fraction of a second there -
    the translation service, not the model, sets the pace of a document.
    Hardware providers stay reachable through set_backend or the
    PDF_TRANSLATE_ONNX_BACKEND variable; "auto" restores every available one.
    """
    name = (name or _preferred_backend or os.environ.get(BACKEND_VARIABLE) or "cpu").lower()
    available = onnxruntime.get_available_providers()
    if name == "auto":
        return list(available) or [CPU_PROVIDER]
    wanted = _BACKEND_PROVIDERS.get(name, _BACKEND_PROVIDERS["cpu"])
    chosen = [provider for provider in wanted if provider in available]
    return chosen or [CPU_PROVIDER]


def has_accelerator(providers: list[str]) -> bool:
    """Whether a session runs nodes anywhere but the CPU."""
    return any(provider not in _PASSIVE_PROVIDERS for provider in providers)


def can_cache_optimized_graph(
    providers: list[str], *, frozen: bool | None = None
) -> bool:
    """Cache only in a mutable source environment, never inside a release."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    compiled_providers = {"CoreMLExecutionProvider", "TensorrtExecutionProvider"}
    return not frozen and not compiled_providers.intersection(providers)


def set_backend(name: str) -> None:
    """Set the ONNX Runtime execution provider backend.

    Args:
        name: One of 'cpu' (the default), 'coreml', 'cuda', 'dml', or 'auto'
            for every provider this onnxruntime build offers.
    """
    global _preferred_backend
    _preferred_backend = name


class DocLayoutModel(abc.ABC):
    @staticmethod
    def load_onnx():
        model = OnnxModel.from_pretrained()
        return model

    @staticmethod
    def load_available():
        return DocLayoutModel.load_onnx()

    @property
    @abc.abstractmethod
    def stride(self) -> int:
        """Stride of the model input."""
        pass

    @abc.abstractmethod
    def predict(self, image, imgsz=1024, **kwargs) -> list:
        """
        Predict the layout of a document page.

        Args:
            image: The image of the document page.
            imgsz: Resize the image to this size. Must be a multiple of the stride.
            **kwargs: Additional arguments.
        """
        pass


class YoloResult:
    """Helper class to store detection results from ONNX model."""

    def __init__(self, boxes, names):
        self.boxes = [YoloBox(data=d) for d in boxes]
        self.boxes.sort(key=lambda x: x.conf, reverse=True)
        self.names = names


class YoloBox:
    """Helper class to store detection results from ONNX model."""

    def __init__(self, data):
        self.xyxy = data[:4]
        self.conf = data[-2]
        self.cls = data[-1]


class OnnxModel(DocLayoutModel):
    def __init__(self, model_path: str):
        model_path = str(model_path)
        self.model_path = model_path
        self._session_lock = threading.Lock()

        providers = preferred_providers()
        try:
            self.model = self._session(providers)
        except Exception as error:
            if not has_accelerator(providers):
                raise
            # A hardware provider that cannot even compile the graph is the
            # same fault as one that cannot run it; the CPU still can.
            logger.warning(
                "ONNX Runtime could not start %s; using the CPU instead: %s",
                providers, error,
            )
            self.model = self._session([CPU_PROVIDER])
        logger.info("ONNX Runtime providers: %s", self.model.get_providers())

        # The session already parsed the model, so the stride and the class
        # names come from it. Reading them with onnx.load instead meant a
        # second full parse of a 75 MB protobuf on every start, and it pulled
        # a package with a long path-traversal history into the import graph
        # for the sake of two strings. The optimized graph keeps the same
        # metadata, so this reads the same values either way.
        metadata = self.model.get_modelmeta().custom_metadata_map
        self._stride = ast.literal_eval(metadata["stride"])
        self._names = ast.literal_eval(metadata["names"])

    def _session(self, providers: list[str]) -> "onnxruntime.InferenceSession":
        model_path = self.model_path
        sess_options = onnxruntime.SessionOptions()
        sess_options.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        # Providers like CoreML generate compiled nodes that cannot be
        # serialized, so only cache the optimized graph for CPU-only.
        if can_cache_optimized_graph(providers):
            optimized_path = model_path + ".optimized"
            if os.path.exists(optimized_path):
                model_path = optimized_path
            else:
                sess_options.optimized_model_filepath = optimized_path
        return onnxruntime.InferenceSession(
            model_path, sess_options, providers=providers
        )

    def run(self, pix: np.ndarray) -> np.ndarray:
        """Run one inference, moving to the CPU for good if an accelerator fails.

        The failure arrives at the first run, not when the session is built, so
        only here can it be caught. The CPU session replaces the accelerated one
        for the rest of the process: every later page would fail the same way.
        """
        session = self.model
        try:
            return session.run(None, {"images": pix})[0]
        except Exception as error:
            if not has_accelerator(session.get_providers()):
                raise LayoutModelError(
                    f"The layout model could not run on the CPU: {error}"
                ) from error
            with self._session_lock:
                if self.model is session:
                    logger.warning(
                        "ONNX Runtime %s failed during layout inference; "
                        "continuing on the CPU: %s",
                        session.get_providers(), error,
                    )
                    self.model = self._session([CPU_PROVIDER])
                session = self.model
        try:
            return session.run(None, {"images": pix})[0]
        except Exception as error:
            raise LayoutModelError(
                f"The layout model could not run on the CPU: {error}"
            ) from error

    @staticmethod
    def from_pretrained():
        pth = get_doclayout_onnx_model_path()
        return OnnxModel(pth)

    @property
    def stride(self):
        return self._stride

    def resize_and_pad_image(self, image, new_shape):
        """
        Resize and pad the image to the specified size, ensuring dimensions are multiples of stride.

        Parameters:
        - image: Input image
        - new_shape: Target size (integer or (height, width) tuple)
        - stride: Padding alignment stride, default 32

        Returns:
        - Processed image
        """
        if isinstance(new_shape, int):
            new_shape = (new_shape, new_shape)

        h, w = image.shape[:2]
        new_h, new_w = new_shape

        # Calculate scaling ratio
        r = min(new_h / h, new_w / w)
        resized_h, resized_w = int(round(h * r)), int(round(w * r))

        # Resize image
        image = cv2.resize(
            image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR
        )

        # Calculate padding size and align to stride multiple
        pad_w = (new_w - resized_w) % self.stride
        pad_h = (new_h - resized_h) % self.stride
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2

        # Add padding
        image = cv2.copyMakeBorder(
            image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )

        return image

    def scale_boxes(self, img1_shape, boxes, img0_shape):
        """
        Rescales bounding boxes (in the format of xyxy by default) from the shape of the image they were originally
        specified in (img1_shape) to the shape of a different image (img0_shape).

        Args:
            img1_shape (tuple): The shape of the image that the bounding boxes are for,
                in the format of (height, width).
            boxes (torch.Tensor): the bounding boxes of the objects in the image, in the format of (x1, y1, x2, y2)
            img0_shape (tuple): the shape of the target image, in the format of (height, width).

        Returns:
            boxes (torch.Tensor): The scaled bounding boxes, in the format of (x1, y1, x2, y2)
        """

        # Calculate scaling ratio
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])

        # Calculate padding size
        pad_x = round((img1_shape[1] - img0_shape[1] * gain) / 2 - 0.1)
        pad_y = round((img1_shape[0] - img0_shape[0] * gain) / 2 - 0.1)

        # Remove padding and scale boxes
        boxes[..., :4] = (boxes[..., :4] - [pad_x, pad_y, pad_x, pad_y]) / gain
        return boxes

    def predict(self, image, imgsz=1024, **kwargs):
        # Preprocess input image
        orig_h, orig_w = image.shape[:2]
        pix = self.resize_and_pad_image(image, new_shape=imgsz)
        pix = np.transpose(pix, (2, 0, 1))  # CHW
        pix = np.expand_dims(pix, axis=0)  # BCHW
        pix = pix.astype(np.float32) / 255.0  # Normalize to [0, 1]
        new_h, new_w = pix.shape[2:]

        # Run inference
        preds = self.run(pix)

        # Postprocess predictions
        preds = preds[preds[..., 4] > 0.25]
        preds[..., :4] = self.scale_boxes(
            (new_h, new_w), preds[..., :4], (orig_h, orig_w)
        )
        return [YoloResult(boxes=preds, names=self._names)]


class ModelInstance:
    value: OnnxModel = None
