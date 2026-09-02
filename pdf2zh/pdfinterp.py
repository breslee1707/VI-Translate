import copy
import logging
import math
from typing import Any, Dict, Optional, Sequence, Tuple, cast

import numpy as np
from pdfminer import settings
from pdfminer.pdfcolor import PREDEFINED_COLORSPACE, PDFColorSpace
from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdffont import PDFFont
from pdfminer.pdfinterp import (
    LITERAL_FORM,
    LITERAL_IMAGE,
    Color,
    PDFContentParser,
    PDFInterpreterError,
    PDFPageInterpreter,
    PDFResourceManager,
    PDFStackT,
)
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import (
    PDFObjRef,
    dict_value,
    list_value,
    resolve1,
    stream_value,
)
from pdfminer.psexceptions import PSEOF
from pdfminer.psparser import (
    PSKeyword,
    PSLiteral,
    keyword_name,
    literal_name,
)
from pdfminer.utils import (
    MATRIX_IDENTITY,
    Matrix,
    Rect,
    apply_matrix_pt,
    mult_matrix,
)

log = logging.getLogger(__name__)

# Colour operators worth replaying in front of translated text, grouped by the
# piece of state each one sets. `g`, `rg`, `k`, `sc` and `scn` all write the
# same non-stroking colour, so only the last of them is in force; replaying one
# of each kind lets a stale `0 g` from earlier in the stream paint over the
# `rg` that is actually current. `cs` selects a space and resets its colour,
# and `sc`/`scn` name components within whatever space is selected, so the two
# travel together.
#
# An ExtGState (`gs`) sets no colour of its own but decides how one composites:
# a brochure whose text is a CMYK black prints pure black only because its
# state turns overprint on. It is replayed only when it cannot hide the text -
# see extgstate_is_safe.
COLOUR_SLOTS = {
    "gs": "ext_state",
    "ri": "intent",
    "cs": "fill_space",
    "CS": "stroke_space",
    "g": "fill_colour",
    "rg": "fill_colour",
    "k": "fill_colour",
    "sc": "fill_colour",
    "scn": "fill_colour",
    "G": "stroke_colour",
    "RG": "stroke_colour",
    "K": "stroke_colour",
    "SC": "stroke_colour",
    "SCN": "stroke_colour",
}
# These name their own space, so any space selected earlier no longer applies.
SELF_CONTAINED_COLOUR_OPERATORS = frozenset({"g", "rg", "k", "G", "RG", "K"})
# A space is replayed before the colour that names components within it.
COLOUR_SLOT_ORDER = (
    "ext_state",
    "intent",
    "fill_space",
    "fill_colour",
    "stroke_space",
    "stroke_colour",
)

# `sc` and `scn` name components inside whichever space is current, so they say
# nothing on their own. Replayed without one, a four-component CMYK black is
# read as the single component of DeviceGray - and DeviceGray 1 is white, which
# is how a chemistry textbook's blue headings turned into blank paper. When the
# space in force is a device space the operand count identifies it exactly;
# when it cannot be identified the colour is dropped rather than guessed, and
# the run falls back to plain black.
DEVICE_SPACE_BY_COMPONENTS = {
    1: "/DeviceGray",
    3: "/DeviceRGB",
    4: "/DeviceCMYK",
}
COMPONENT_COLOUR_OPERATORS = frozenset({"sc", "scn", "SC", "SCN"})


# An ExtGState carrying any of these can make the text invisible or invert it,
# which is never what replaying a colour is meant to do.
CONCEALING_EXTGSTATE_KEYS = ("SMask", "TR", "TR2")
HARMLESS_EXTGSTATE_VALUES = ("None", "Identity", "Default")


def extgstate_is_safe(state: Dict[Any, Any]) -> bool:
    """Whether replaying this graphics state can only affect how colour mixes.

    Overprint and blend mode belong to the text and have to travel with it.
    A soft mask, a transfer function or partial alpha do not: replaying one in
    front of the translation could leave the page blank, and a page that reads
    as empty is worse than one whose black is a shade off.
    """
    for key in CONCEALING_EXTGSTATE_KEYS:
        value = resolve1(state.get(key))
        if value is None:
            continue
        if (
            isinstance(value, PSLiteral)
            and literal_name(value) in HARMLESS_EXTGSTATE_VALUES
        ):
            continue
        return False
    for key in ("ca", "CA"):
        value = resolve1(state.get(key, 1))
        try:
            if float(value) < 1:
                return False
        except (TypeError, ValueError):
            return False
    return True


class PDFPageInterpreterEx(PDFPageInterpreter):
    """Processor for the content of a PDF page

    Reference: PDF Reference, Appendix A, Operator Summary
    """

    def __init__(
        self, rsrcmgr: PDFResourceManager, device: PDFDevice, obj_patch
    ) -> None:
        self.rsrcmgr = rsrcmgr
        self.device = device
        self.obj_patch = obj_patch

    def dup(self) -> "PDFPageInterpreterEx":
        return self.__class__(self.rsrcmgr, self.device, self.obj_patch)

    def record_graphic(self, name: str, args: Sequence[object]) -> None:
        """Hand one colour operator to the device, verbatim.

        Replaying the operator text is the only way to carry a colour across
        faithfully: a `scn` in an ICCBased or Separation space means nothing
        without the `cs` that selected it, and reducing everything to RGB would
        guess at the conversion the document never asked for. BabelDOC carries
        colour the same way.
        """
        slot = COLOUR_SLOTS.get(name)
        if slot is None:
            return
        if name == "gs" and not self.named_extgstate_is_safe(args):
            return
        if name in COMPONENT_COLOUR_OPERATORS and not self.device.knows_colour_space(
            slot.replace("_colour", "_space")
        ):
            space = DEVICE_SPACE_BY_COMPONENTS.get(len(args))
            if space is None or any(isinstance(value, PSLiteral) for value in args):
                # A pattern, or a component count no device space explains.
                # Emitting it against the wrong space would be worse than
                # drawing the run in the colour a page starts in.
                self.device.forget_colour(slot)
                return
            self.device.record_graphic_operator(
                slot.replace("_colour", "_space"),
                f"{space} {'cs' if slot.startswith('fill') else 'CS'}",
                False,
            )
        text = " ".join(
            f"{value:f}" if isinstance(value, float) else str(value).replace("'", "")
            for value in args
        )
        self.device.record_graphic_operator(
            slot, f"{text} {name}".strip(), name in SELF_CONTAINED_COLOUR_OPERATORS
        )

    def named_extgstate_is_safe(self, args: Sequence[object]) -> bool:
        try:
            states = dict_value(self.resources.get("ExtGState"))
            state = dict_value(states[literal_name(args[0])])
        except Exception:
            return False
        return extgstate_is_safe(state)

    def do_q(self) -> None:
        """Save graphics state"""
        super().do_q()
        self.device.push_graphic_state()

    def do_Q(self) -> None:
        """Restore graphics state"""
        super().do_Q()
        self.device.pop_graphic_state()

    def init_resources(self, resources: Dict[object, object]) -> None:
        """Prepare the fonts and XObjects listed in the Resource attribute."""
        self.resources = resources
        self.fontmap: Dict[object, PDFFont] = {}
        self.fontid: Dict[PDFFont, object] = {}
        self.xobjmap = {}
        self.csmap: Dict[str, PDFColorSpace] = PREDEFINED_COLORSPACE.copy()
        if not resources:
            return

        def get_colorspace(spec: object) -> Optional[PDFColorSpace]:
            if isinstance(spec, list):
                name = literal_name(spec[0])
            else:
                name = literal_name(spec)
            if name == "ICCBased" and isinstance(spec, list) and len(spec) >= 2:
                return PDFColorSpace(name, stream_value(spec[1])["N"])
            elif name == "DeviceN" and isinstance(spec, list) and len(spec) >= 2:
                return PDFColorSpace(name, len(list_value(spec[1])))
            else:
                return PREDEFINED_COLORSPACE.get(name)

        for k, v in dict_value(resources).items():
            # log.debug("Resource: %r: %r", k, v)
            if k == "Font":
                for fontid, spec in dict_value(v).items():
                    objid = None
                    if isinstance(spec, PDFObjRef):
                        objid = spec.objid
                    spec = dict_value(spec)
                    self.fontmap[fontid] = self.rsrcmgr.get_font(objid, spec)
                    self.fontmap[fontid].descent = 0  # hack fix descent
                    self.fontid[self.fontmap[fontid]] = fontid
            elif k == "ColorSpace":
                for csid, spec in dict_value(v).items():
                    colorspace = get_colorspace(resolve1(spec))
                    if colorspace is not None:
                        self.csmap[csid] = colorspace
            elif k == "ProcSet":
                self.rsrcmgr.get_procset(list_value(v))
            elif k == "XObject":
                for xobjid, xobjstrm in dict_value(v).items():
                    self.xobjmap[xobjid] = xobjstrm

    def do_S(self) -> None:
        """Stroke path"""

        def is_black(color: Color) -> bool:
            if color is None:
                # TeX never emits a stroking-colour operator for a fraction
                # rule, and PDF's initial stroking colour is black. Reading the
                # unset state as "not black" left every inline fraction bar
                # behind at its source position, striking through the reflowed
                # translation.
                return True
            if isinstance(color, Tuple):
                return sum(color) == 0
            else:
                return color == 0

        if (
            len(self.curpath) == 2
            and self.curpath[0][0] == "m"
            and self.curpath[1][0] == "l"
            and apply_matrix_pt(self.ctm, self.curpath[0][-2:])[1]
            == apply_matrix_pt(self.ctm, self.curpath[1][-2:])[1]
            and is_black(self.graphicstate.scolor)
        ):
            # pdfminer records the raw `w` operand, but the path is drawn
            # under this CTM (TeX scales by 0.1), and the redrawn rule gets no
            # CTM. Bake the scale in or the bar comes out ten times too fat.
            gs = copy.copy(self.graphicstate)
            gs.linewidth *= math.hypot(self.ctm[0], self.ctm[1])
            self.device.paint_path(gs, True, False, False, self.curpath)
            self.curpath = []
            return "n"
        else:
            self.curpath = []

    ############################################################
    def do_f(self) -> None:
        """Fill path using nonzero winding number rule"""
        # self.device.paint_path(self.graphicstate, False, True, False, self.curpath)
        self.curpath = []

    def do_F(self) -> None:
        """Fill path using nonzero winding number rule (obsolete)"""

    def do_f_a(self) -> None:
        """Fill path using even-odd rule"""
        # self.device.paint_path(self.graphicstate, False, True, True, self.curpath)
        self.curpath = []

    def do_B(self) -> None:
        """Fill and stroke path using nonzero winding number rule"""
        # self.device.paint_path(self.graphicstate, True, True, False, self.curpath)
        self.curpath = []

    def do_B_a(self) -> None:
        """Fill and stroke path using even-odd rule"""
        # self.device.paint_path(self.graphicstate, True, True, True, self.curpath)
        self.curpath = []

    ############################################################
    def do_SCN(self) -> None:
        """Set color for stroking operations."""
        if self.scs:
            n = self.scs.ncomponents
        else:
            if settings.STRICT:
                raise PDFInterpreterError("No colorspace specified!")
            n = 1
        args = self.pop(n)
        self.graphicstate.scolor = cast(Color, args)
        return args

    def do_scn(self) -> None:
        """Set color for nonstroking operations"""
        if self.ncs:
            n = self.ncs.ncomponents
        else:
            if settings.STRICT:
                raise PDFInterpreterError("No colorspace specified!")
            n = 1
        args = self.pop(n)
        self.graphicstate.ncolor = cast(Color, args)
        return args

    def do_SC(self) -> None:
        """Set color for stroking operations"""
        return self.do_SCN()

    def do_sc(self) -> None:
        """Set color for nonstroking operations"""
        return self.do_scn()

    def do_Do(self, xobjid_arg: PDFStackT) -> None:
        """Invoke named XObject"""
        xobjid = literal_name(xobjid_arg)
        try:
            xobj = stream_value(self.xobjmap[xobjid])
        except KeyError:
            if settings.STRICT:
                raise PDFInterpreterError("Undefined xobject id: %r" % xobjid)
            return
        # log.debug("Processing xobj: %r", xobj)
        subtype = xobj.get("Subtype")
        if subtype is LITERAL_FORM and "BBox" in xobj:
            interpreter = self.dup()
            bbox = cast(Rect, list_value(xobj["BBox"]))
            matrix = cast(Matrix, list_value(xobj.get("Matrix", MATRIX_IDENTITY)))
            # According to PDF reference 1.7 section 4.9.1, XObjects in
            # earlier PDFs (prior to v1.2) use the page's Resources entry
            # instead of having their own Resources entry.
            xobjres = xobj.get("Resources")
            if xobjres:
                resources = dict_value(xobjres)
            else:
                resources = self.resources.copy()
            self.device.begin_figure(xobjid, bbox, matrix)
            ctm = mult_matrix(matrix, self.ctm)
            ops_base = interpreter.render_contents(
                resources,
                [xobj],
                ctm=ctm,
            )
            try:
                self.device.fontid = interpreter.fontid
                self.device.fontmap = interpreter.fontmap
                ops_new = self.device.end_figure(xobjid)
                ctm_inv = np.linalg.inv(np.array(ctm[:4]).reshape(2, 2))
                np_version = np.__version__
                if np_version.split(".")[0] >= "2":
                    pos_inv = -np.asmatrix(ctm[4:]) * ctm_inv
                else:
                    pos_inv = -np.mat(ctm[4:]) * ctm_inv
                a, b, c, d = ctm_inv.reshape(4).tolist()
                e, f = pos_inv.tolist()[0]
                self.obj_patch[self.xobjmap[xobjid].objid] = (
                    f"q {ops_base}Q {a} {b} {c} {d} {e} {f} cm {ops_new}"
                )
            except Exception:
                pass
        elif subtype is LITERAL_IMAGE and "Width" in xobj and "Height" in xobj:
            self.device.begin_figure(xobjid, (0, 0, 1, 1), MATRIX_IDENTITY)
            self.device.render_image(xobjid, xobj)
            self.device.end_figure(xobjid)
        else:
            # unsupported xobject type.
            pass

    def process_page(self, page: PDFPage) -> None:
        # log.debug("Processing page: %r", page)
        # print(page.mediabox,page.cropbox)
        # (x0, y0, x1, y1) = page.mediabox
        x0, y0, x1, y1 = page.cropbox
        if page.rotate == 90:
            ctm = (0, -1, 1, 0, -y0, x1)
        elif page.rotate == 180:
            ctm = (-1, 0, 0, -1, x1, y1)
        elif page.rotate == 270:
            ctm = (0, 1, -1, 0, y1, -x0)
        else:
            ctm = (1, 0, 0, 1, -x0, -y0)
        self.device.begin_page(page, ctm)
        ops_base = self.render_contents(page.resources, page.contents, ctm=ctm)
        self.device.fontid = self.fontid
        self.device.fontmap = self.fontmap
        ops_new = self.device.end_page(page)
        self.obj_patch[page.page_xref] = (
            f"q {ops_base}Q 1 0 0 1 {x0} {y0} cm {ops_new}"
        )
        for obj in page.contents:
            self.obj_patch[obj.objid] = ""

    def render_contents(
        self,
        resources: Dict[object, object],
        streams: Sequence[object],
        ctm: Matrix = MATRIX_IDENTITY,
    ) -> None:
        """Render the content streams.

        This method may be called recursively.
        """
        # log.debug(
        #     "render_contents: resources=%r, streams=%r, ctm=%r",
        #     resources,
        #     streams,
        #     ctm,
        # )
        self.init_resources(resources)
        self.init_state(ctm)
        return self.execute(list_value(streams))

    def execute(self, streams: Sequence[object]) -> None:
        ops = ""
        try:
            parser = PDFContentParser(streams)
        except PSEOF:
            # empty page
            return
        while True:
            try:
                _, obj = parser.nextobject()
            except PSEOF:
                break
            if isinstance(obj, PSKeyword):
                name = keyword_name(obj)
                method = "do_%s" % name.replace("*", "_a").replace('"', "_w").replace(
                    "'",
                    "_q",
                )
                if hasattr(self, method):
                    func = getattr(self, method)
                    nargs = func.__code__.co_argcount - 1
                    if nargs:
                        args = self.pop(nargs)
                        # log.debug("exec: %s %r", name, args)
                        if len(args) == nargs:
                            func(*args)
                            self.record_graphic(name, args)
                            if not (
                                name[0] == "T"
                                or name in ['"', "'", "EI", "MP", "DP", "BMC", "BDC"]
                            ):
                                p = " ".join(
                                    [
                                        (
                                            f"{x:f}"
                                            if isinstance(x, float)
                                            else str(x).replace("'", "")
                                        )
                                        for x in args
                                    ]
                                )
                                ops += f"{p} {name} "
                    else:
                        # log.debug("exec: %s", name)
                        targs = func()
                        if targs is None:
                            targs = []
                        # `sc`/`scn` pop their own operands, so they arrive
                        # here rather than in the branch above.
                        self.record_graphic(name, targs)
                        if not (name[0] == "T" or name in ["BI", "ID", "EMC"]):
                            p = " ".join(
                                [
                                    (
                                        f"{x:f}"
                                        if isinstance(x, float)
                                        else str(x).replace("'", "")
                                    )
                                    for x in targs
                                ]
                            )
                            ops += f"{p} {name} "
                elif settings.STRICT:
                    error_msg = "Unknown operator: %r" % name
                    raise PDFInterpreterError(error_msg)
            else:
                self.push(obj)
        # print('REV DATA',ops)
        return ops
