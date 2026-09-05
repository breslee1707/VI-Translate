<p align="center">
  <img src=".github/assets/logo.png" alt="PDF Translate logo" width="160">
</p>

<h1 align="center">PDF Translate</h1>

<p align="center">
  <strong>Dịch tài liệu PDF sang tiếng Việt và 35 ngôn ngữ khác<br>mà vẫn giữ nguyên bố cục, công thức, bảng và hình ảnh.</strong>
</p>

<p align="center">
  <sub>Xây dựng và duy trì bởi <a href="https://www.tiktok.com/@huyg.ai">Lê Ngọc Gia Huy · huyg.ai</a></sub>
</p>

<p align="center">
  <a href="https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-windows.zip">
    <img src="https://img.shields.io/badge/TẢI_XUỐNG-Windows_x64-1f6feb?style=for-the-badge&logo=windows11&logoColor=white" alt="Tải PDF Translate cho Windows">
  </a>
  <a href="https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-macos-apple-silicon.dmg">
    <img src="https://img.shields.io/badge/TẢI_XUỐNG-macOS_Apple_Silicon-111111?style=for-the-badge&logo=apple&logoColor=white" alt="Tải PDF Translate cho Mac Apple Silicon">
  </a>
  <a href="https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-macos-intel.dmg">
    <img src="https://img.shields.io/badge/TẢI_XUỐNG-macOS_Intel-555555?style=for-the-badge&logo=apple&logoColor=white" alt="Tải PDF Translate cho Mac Intel">
  </a>
</p>

<p align="center">
  <a href="https://github.com/breslee1707/VI-Translate/releases/latest"><img src="https://img.shields.io/github/v/release/breslee1707/VI-Translate?style=flat-square&label=release" alt="Bản phát hành mới nhất"></a>
  <a href="https://github.com/breslee1707/VI-Translate/releases"><img src="https://img.shields.io/github/downloads/breslee1707/VI-Translate/total?style=flat-square&label=downloads" alt="Tổng lượt tải"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/breslee1707/VI-Translate?style=flat-square" alt="Giấy phép AGPL-3.0"></a>
  <img src="https://img.shields.io/badge/Python-không_cần_cài-2ea44f?style=flat-square" alt="Không cần cài Python">
</p>

<p align="center">
  <a href="#điểm-nổi-bật">Điểm nổi bật</a> ·
  <a href="#bắt-đầu-trong-1-phút">Cài đặt</a> ·
  <a href="#cách-sử-dụng">Cách dùng</a> ·
  <a href="#được-phát-triển-ở-đây">Phát triển</a> ·
  <a href="#dùng-như-agent-skill">Agent Skill</a> ·
  <a href="#giới-hạn-hiện-tại">Giới hạn</a>
</p>

---

PDF Translate là ứng dụng mã nguồn mở dành cho Windows, macOS và Android. Công cụ phân tích bố cục từng trang, bảo vệ công thức và code, dịch phần văn xuôi rồi đặt nội dung trở lại đúng vị trí trong tài liệu gốc — không biến PDF của bạn thành một trang chữ trắng đơn giản.

Đây không phải bản dịch giao diện của một công cụ có sẵn. Dự án mượn ý tưởng và phần nhân đọc/ghi PDF từ hai dự án mã nguồn mở, rồi tự xây phần quyết định chất lượng đầu ra: ứng dụng desktop, bộ quy tắc bảo toàn bố cục, lớp xử lý tiếng Việt và hàng loạt bản sửa lỗi ngay trong nhân. Chi tiết ở mục [Được phát triển ở đây](#được-phát-triển-ở-đây).

## Điểm nổi bật

- **Giữ nguyên bố cục:** bảo toàn vị trí của đoạn văn, công thức, bảng, hình, mục lục và tài liệu tham khảo.
- **Sẵn sàng để dùng:** tải về, giải nén và chạy; không cần cài Python hay model riêng.
- **Xử lý hàng loạt:** kéo thả nhiều file PDF hoặc cả thư mục vào ứng dụng.
- **OCR cục bộ cho trang scan:** chế độ Tự động đọc và dịch vùng văn bản an
  toàn; bảng, công thức và hình phức tạp được giữ nguyên thay vì bị phá bố cục.
- **36 ngôn ngữ đích:** mặc định là tiếng Việt, cùng nhiều ngôn ngữ sử dụng chữ Latin.
- **Không dừng cả hàng đợi:** một file lỗi không làm gián đoạn các file còn lại.
- **Tự cập nhật (Windows):** có bản mới thì ứng dụng tự tải ngầm, bạn chỉ cần bấm một lần để khởi động lại. Trên macOS vẫn là dòng nhắc mở trang tải.
- **Có chế độ dành cho AI agent:** dùng model trong Codex, Claude Code hoặc Copilot để dịch tài liệu chuyên ngành tốt hơn.

## Bắt đầu trong 1 phút

### Windows

1. **[Tải PDF Translate cho Windows](https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-windows.zip)** (`.zip`, khoảng 385 MB; đã gồm model OCR chạy cục bộ).
2. Giải nén toàn bộ file vừa tải.
3. Mở `PDFTranslate.exe`.

### macOS

1. Tải bản phù hợp: **[Apple Silicon](https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-macos-apple-silicon.dmg)** cho Mac M1/M2/M3/M4/M5 hoặc **[Intel](https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-macos-intel.dmg)** cho Mac Intel.
2. Mở file `.dmg`, kéo **PDF Translate** vào thư mục **Applications**.
3. Trong lần chạy đầu, bấm chuột phải vào ứng dụng → **Open** → **Open**.

### Android

1. **[Tải APK từ bản phát hành Android mới nhất](https://github.com/breslee1707/VI-Translate/releases?q=android-v)** (`PDFTranslate-android-*.apk`). Bản Android được đánh tag riêng theo dạng `android-v*`.
2. Trên điện thoại, cho phép trình duyệt hoặc trình quản lý file **cài ứng dụng từ nguồn không xác định**, rồi mở file APK.
3. Ứng dụng cần Android 8.0 trở lên.

Muốn tự biên dịch:

```bash
cd android
./gradlew assembleDebug
```

> [!NOTE]
> Bản macOS yêu cầu **macOS 14 Sonoma trở lên**. Tất cả bản desktop đều không cần cài Python và không phải tải thêm model ở lần chạy đầu. Quá trình dịch bằng Google vẫn cần kết nối Internet.

> [!IMPORTANT]
> Bản Android là một bản viết lại trên PDFBox-Android, không phải bản desktop biên dịch lại. Nó chỉ đọc PDF dạng văn bản (chưa có OCR) và dùng heuristic thay cho model bố cục BabelDOC/YOLOv8, nên PDF nhiều công thức có thể còn sai chỉ số trên/dưới hoặc phân số. Chi tiết build và phát hành nằm ở [`docs/android-release.md`](docs/android-release.md).

> [!WARNING]
> Windows SmartScreen có thể cảnh báo vì ứng dụng chưa được ký số. Chọn **More info** → **Run anyway** nếu bạn tải file từ trang Releases chính thức của repo này.

> [!WARNING]
> Nếu máy bạn bật **Smart App Control** (mặc định trên các máy Windows 11 cài mới), ứng dụng sẽ bị **chặn hẳn** với thông báo *"An Application Control policy has blocked this file"* — không có nút Run anyway. Kiểm tra tại **Windows Security → App & browser control → Smart App Control**. Đây là hệ quả của việc ứng dụng chưa có chữ ký số, không phải lỗi của ứng dụng. Microsoft chỉ cho bật lại Smart App Control bằng cách cài lại Windows, nên hãy cân nhắc kỹ trước khi tắt nó; giải pháp đúng là ký số ứng dụng, và việc đó đang được xử lý.

> [!WARNING]
> Bản macOS hiện dùng chữ ký ad-hoc, chưa được Apple notarize. Gatekeeper có thể chặn thao tác mở thông thường; hãy dùng cách bấm chuột phải → **Open** ở trên nếu bạn tải từ trang Releases chính thức.

Bạn cũng có thể mở [trang Releases](https://github.com/breslee1707/VI-Translate/releases/latest) để xem ghi chú thay đổi và các tệp của phiên bản mới nhất.

Ứng dụng cũng tự kiểm tra phiên bản mới mỗi lần mở. Máy không có mạng thì bỏ qua, không báo lỗi.

Trên **Windows**, bản mới được tải ngầm ngay trong lúc bạn vẫn dịch bình thường; góc trên bên phải hiện **↓ Đang tải vX.Y.Z**, tải xong đổi thành **● Cài vX.Y.Z & khởi động lại**. Bấm vào đó, ứng dụng đóng lại, thay thư mục cài đặt rồi tự mở lại — khoảng mười giây, không phải giải nén gì. Đang dịch dở thì nút chờ đến khi xong. Tải xong mà bạn tắt ứng dụng luôn cũng không mất: lần mở sau vẫn là nút khởi động lại, không tải lại từ đầu. Nếu bước thay thư mục hỏng giữa chừng, bản cũ được đưa lại nguyên vẹn và bạn tải thủ công như trước.

Trên **macOS**, và khi ứng dụng nằm ở thư mục không có quyền ghi, dòng đó vẫn là **● Có bản mới vX.Y.Z** và bấm vào sẽ mở trang tải.

## Cách sử dụng

### 1. Thêm tài liệu

Chọn một trong ba cách:

- Kéo thả file PDF hoặc cả thư mục vào cửa sổ ứng dụng.
- Bấm **Chọn file** hoặc **Chọn thư mục**.
- Thả file trực tiếp lên biểu tượng ứng dụng.

### 2. Chọn ngôn ngữ

Chọn ngôn ngữ đích trong mục **Dịch sang**. Ứng dụng mặc định dịch sang **Tiếng Việt**.

Với PDF scan, giữ **Trang ảnh scan → Tự động (khuyên dùng)**. Chế độ **Nâng
cao** chậm hơn đáng kể và chủ yếu dùng để thử nghiệm; **Tắt OCR** nếu bạn chỉ
muốn xử lý lớp chữ có sẵn. Một trang có bảng, công thức, hình hoặc nhận dạng
không chắc chắn sẽ được giữ nguyên và hàng đợi báo **dịch một phần**.

### 3. Bắt đầu dịch

Bấm **Dịch**. Các file được xử lý lần lượt và hiển thị trạng thái ngay trong hàng đợi.

Kết quả được lưu tự động vào thư mục `translated` nằm cạnh file nguồn:

```text
TaiLieu/
├── document.pdf
└── translated/
    └── document-vi.pdf
```

Mặc định, ứng dụng không ghi đè kết quả đã có. Bật **Ghi đè file đã dịch trước đó** khi bạn muốn dịch lại.

## Được phát triển ở đây

Phần kế thừa từ dự án gốc là nhân đọc, phân tích bố cục và render PDF. Mọi thứ
quyết định việc một trang tiếng Việt in ra có đọc được hay không đều được viết
cho dự án này:

**Ứng dụng desktop — 1.620 dòng trong [`app/`](app/)**
Giao diện, kéo thả, hàng đợi nhiều file không dừng vì một file lỗi, mã lỗi đọc
được kèm log để người dùng báo lỗi được, cơ chế tự cập nhật tự thay thư mục cài
đặt rồi mở lại app, đóng gói PyInstaller cho Windows và cả hai kiến trúc macOS,
workflow phát hành tự động. Không một dòng nào trong thư mục này đến từ dự án
gốc.

**Bộ quy tắc bảo toàn — [`pdf2zh/rules.py`](pdf2zh/rules.py) (519 dòng) và [contract sản phẩm](references/preservation-rules.md)**
Đây là thứ phân biệt dự án với một công cụ dịch PDF thông thường: nhận diện vùng
công thức và ký hiệu kỹ thuật để không dịch nhầm thành văn xuôi, giữ in đậm và
in nghiêng qua vòng dịch, dựng lại chữ xoay 90 độ đúng chiều thay vì bẻ ngang,
giữ bullet Wingdings/Symbol mà font Unicode không có glyph, tính chiều cao dòng
tối thiểu cho chữ tiếng Việt nhiều dấu chồng.

**Những lỗi tự tìm và tự sửa trong nhân**
Mỗi lỗi dưới đây được phát hiện từ tài liệu thật, truy ra nguyên nhân trong nhân
và sửa tại đây:

- Tiếng Việt mất sạch chữ có dấu chồng — *"Việt"* in ra thành *"Vi t"* — vì
  `subset_fonts` đánh số lại glyph trong khi content stream gọi theo ID gốc.
- Sách dài dừng ngay trước trang cuối vì tài liệu bị nhân đôi rồi nén lại toàn bộ
  ở bước kết thúc.
- Cả trang lộn ngược vì ma trận chữ phản chiếu `1 0 0 -1` bị hiểu nhầm là xoay.
- Đoạn văn in đè lên đoạn bên dưới vì ngân sách chiều cao chỉ tính hộp của chính nó.

Bảng nguyên nhân đầy đủ: [agent-knowledge/regressions.md](agent-knowledge/regressions.md).

**Bộ test hồi quy trong [`tests/`](tests/)**
Mỗi lỗi đã sửa đều bị khoá lại bằng một test dựng đúng hình học nhỏ nhất gây ra nó,
nên bản sửa không âm thầm mất đi ở lần thay đổi sau.

**Chế độ Handoff và Agent Skill**
Một engine dịch thứ hai đưa các đoạn văn sang JSONL cho AI agent dịch theo ngữ
cảnh chuyên ngành rồi dựng lại PDF, cùng chuẩn `SKILL.md` để gọi trực tiếp trong
Codex, Claude Code hay Copilot.

## Ngôn ngữ hỗ trợ

Ứng dụng hỗ trợ 36 ngôn ngữ sử dụng chữ Latin, gồm tiếng Việt, Anh, Pháp, Đức, Tây Ban Nha, Bồ Đào Nha, Ý, Indonesia, Hà Lan, Ba Lan, Thổ Nhĩ Kỳ và nhiều ngôn ngữ châu Âu khác.

Các hệ chữ sau chưa được hỗ trợ: Trung, Nhật, Hàn, Ả Rập, Do Thái, Thái và các chữ Ấn Độ. Ứng dụng sẽ báo lỗi thay vì tạo PDF chứa ký tự ô vuông do thiếu glyph.

## Dùng như Agent Skill

Repo đồng thời tuân theo chuẩn [Agent Skills](https://agentskills.io/) và có thể dùng với Codex, Claude Code, GitHub Copilot cùng các coding agent hỗ trợ `SKILL.md`.

Cài skill cho tất cả agent có trên máy:

```powershell
npx skills add breslee1707/VI-Translate -g --all
```

Sau đó gọi skill bằng yêu cầu tự nhiên:

```text
Use $pdf-translate to translate this PDF into Vietnamese.
```

Trong Claude Code hoặc Copilot CLI:

```text
/pdf-translate translate this PDF into Vietnamese.
```

Skill cung cấp hai chế độ dịch:

| Chế độ | Bộ máy dịch | Phù hợp khi |
| --- | --- | --- |
| **Google** | `translate.google.com` | Cần nhanh, miễn phí và không có API key |
| **Handoff** | AI agent trong phiên làm việc | Tài liệu chuyên ngành cần bản dịch theo ngữ cảnh |

Google là chế độ mặc định và là chế độ được dùng trong app desktop. Handoff trích các đoạn văn sang JSONL để agent dịch, sau đó dựng lại PDF; dữ liệu không được gửi tới Google nhưng sẽ tốn token và mất nhiều thời gian hơn.

Ví dụ với từ *conduction* trong tài liệu truyền nhiệt:

| Google | Handoff |
| --- | --- |
| “Sự **dẫn điện** xảy ra khi hai vật tiếp xúc trực tiếp” | “**Dẫn nhiệt** xảy ra khi hai vật thể tiếp xúc trực tiếp với nhau” |

Xem quy trình đầy đủ tại [SKILL.md](SKILL.md).

## Chạy từ mã nguồn

### Chuẩn bị môi trường

```powershell
git clone https://github.com/breslee1707/VI-Translate.git
cd VI-Translate
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Dịch bằng Google

```powershell
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --output-dir OUT
```

### Dịch bằng Handoff

```powershell
# 1. Trích các đoạn cần dịch
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --engine handoff --emit-segments segments.jsonl

# 2. Dùng agent dịch segments.jsonl thành translations.jsonl

# 3. Dựng lại PDF
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --engine handoff --segments translations.jsonl --output-dir OUT
```

### Build ứng dụng Windows

```powershell
.\build.ps1
```

Gói phát hành được tạo tại `dist\PDFTranslate-windows.zip`.

### Build ứng dụng macOS

Chạy trên máy Mac dùng đúng kiến trúc cần phát hành:

```bash
bash build-macos.sh
```

Gói phát hành được tạo tại `dist/PDFTranslate-macos-apple-silicon.dmg` hoặc `dist/PDFTranslate-macos-intel.dmg`. Từ máy Windows, bạn có thể chạy thủ công workflow **Release** trên GitHub Actions để lấy cả hai DMG trong phần Artifacts; khi push tag `v*`, workflow tự đính kèm chúng vào GitHub Release.

## Giới hạn hiện tại

- **OCR ưu tiên an toàn:** văn bản scan thông thường có thể được dịch, nhưng
  trang có bảng/lưới, công thức, hình phức tạp, code hoặc thứ tự đọc không chắc
  chắn sẽ được giữ nguyên và báo là dịch một phần.
- Chữ nằm trong vùng được nhận diện là bảng hoặc hình đôi khi được giữ nguyên theo bản gốc.
- Mục lục, index, danh mục ký hiệu và tài liệu tham khảo được ưu tiên giữ bố cục nên không được dàn lại dòng. Xem [quy tắc bảo toàn](references/preservation-rules.md).
- Mỗi đoạn gửi tới Google được giới hạn ở 5.000 ký tự; phần vượt quá giới hạn không được dịch.
- Với đoạn vốn quá chật, mẫu số của phân số nội dòng có thể vẫn nằm sát dòng bên dưới.

Nên kiểm tra lại tài liệu đầu ra trước khi dùng cho xuất bản hoặc các mục đích yêu cầu độ chính xác cao.

## Giấy phép và ghi công

PDF Translate được phát hành theo giấy phép [AGPL-3.0](LICENSE). Nếu phát hành lại ứng dụng hoặc cung cấp nó như một dịch vụ qua mạng, bạn phải kèm theo mã nguồn tương ứng theo điều khoản của giấy phép.

Thư mục [`pdf2zh/`](pdf2zh/) là bản fork của nhân xử lý PDF từ [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) 1.9.11, dùng model bố cục và font đã ghim của [BabelDOC](https://github.com/funstory-ai/BabelDOC). Hai dự án đó cho công việc này một điểm khởi đầu và được ghi công đầy đủ tại [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Ứng dụng desktop, bộ quy tắc bảo toàn, các bản sửa lỗi trong nhân, bộ test hồi quy, chế độ Handoff và toàn bộ phần đóng gói — mọi thứ liệt kê ở [Được phát triển ở đây](#được-phát-triển-ở-đây) — do **Lê Ngọc Gia Huy** ([@huyg.ai trên TikTok](https://www.tiktok.com/@huyg.ai)) phát triển và duy trì.

---

<p align="center">
  Nếu PDF Translate hữu ích với bạn, hãy tặng repo một ⭐ để nhiều người biết đến dự án hơn.
</p>

<p align="center">
  <sub>Xây dựng &amp; duy trì bởi <a href="https://www.tiktok.com/@huyg.ai">Lê Ngọc Gia Huy (huyg.ai)</a></sub>
</p>
