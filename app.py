import streamlit as st
try:
    import pymupdf as fitz          # PyMuPDF 1.24.3+
except ImportError:
    import fitz                     # 구버전 호환
import re
import os
import urllib.request
import requests

FONT_NAME = "kfont"
LINE_RATIO = 1.32                   # 줄간격 배수
CM_TO_PT = 28.3465
A4_W, A4_H = 841.89, 595.28         # A4 가로 (pt) — 모든 슬라이드를 이 크기로 통일


# --- 글씨체 목록 (모두 무료 OFL 폰트, jsDelivr 버전 고정 주소) ---
FONTS = {
    "Pretendard": (
        "Pretendard-Regular.ttf",
        "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/public/static/alternative/Pretendard-Regular.ttf",
    ),
    "고운돋움": (
        "GowunDodum-Regular.ttf",
        "https://cdn.jsdelivr.net/npm/@expo-google-fonts/gowun-dodum@0.4.1/400Regular/GowunDodum_400Regular.ttf",
    ),
    "IBM Plex Sans KR": (
        "IBMPlexSansKR-Regular.ttf",
        "https://cdn.jsdelivr.net/npm/@expo-google-fonts/ibm-plex-sans-kr@0.4.1/400Regular/IBMPlexSansKR_400Regular.ttf",
    ),
}
EMPH_RE = re.compile(r"\*\*(.+?)\*\*|==(.+?)==", re.S)   # **강조** 또는 ==강조==
EMPH_COLOR = (0.80, 0.05, 0.05)     # 강조 글씨색 (빨강)
EMPH_BG = (1.0, 0.93, 0.35)         # 형광펜색 (노랑)
TEXT_COLOR = (0, 0, 0)

FALLBACK_LABEL = "Pretendard"       # 선택한 폰트에 없는 글자(ç, α 등)는 이 폰트로 찍음
FB_NAME = "kfont_fb"


def _is_valid_font(path):
    try:
        fitz.Font(fontfile=path)
        return True
    except Exception:
        return False


# --- 글씨체 자동 다운로드 (받은 파일이 깨졌으면 지우고 다시 받음) ---
@st.cache_resource
def get_korean_font(font_label="Pretendard"):
    file_name, url = FONTS[font_label]
    if os.path.exists(file_name) and _is_valid_font(file_name):
        return file_name
    tmp = file_name + ".part"
    urllib.request.urlretrieve(url, tmp)
    if not _is_valid_font(tmp):
        os.remove(tmp)
        raise RuntimeError(f"'{font_label}' 글씨체를 받지 못했습니다. 잠시 후 다시 시도해 주세요.")
    os.replace(tmp, file_name)
    return file_name


# --- 글자 폭 측정기 ---
# 글자마다 (폭, 어느 폰트로 찍을지)를 한 번만 계산해 저장 → 속도 + 빠진 글자 대체
class TextMeasurer:
    def __init__(self, main_path, fb_path):
        self.main = fitz.Font(fontfile=main_path)
        self.fb = fitz.Font(fontfile=fb_path)
        self.cache = {}

    def info(self, ch):
        v = self.cache.get(ch)
        if v is None:
            code = ord(ch)
            if self.main.has_glyph(code) or not self.fb.has_glyph(code):
                v = (self.main.glyph_advance(code), False)
            else:
                v = (self.fb.glyph_advance(code), True)
            self.cache[ch] = v
        return v

    def width(self, text, size):
        return sum(self.info(ch)[0] for ch in text) * size

    def runs(self, text):
        """같은 폰트로 찍을 글자끼리 묶어서 [(문자열, 대체폰트여부), ...] 반환"""
        out = []
        for ch in text:
            use_fb = self.info(ch)[1]
            if out and out[-1][1] == use_fb:
                out[-1][0] += ch
            else:
                out.append([ch, use_fb])
        return out


# --- 대본 파싱 ---
def parse_script_text(text):
    splits = re.split(r'\[(\d+)\s*페이지\]', text)
    script_dict = {}
    for i in range(1, len(splits), 2):
        if i + 1 < len(splits):
            page_num = int(splits[i])
            content = splits[i + 1].strip()
            if page_num in script_dict:
                script_dict[page_num] += "\n" + content
            else:
                script_dict[page_num] = content
    return script_dict


def parse_script_bytes(txt_bytes):
    try:
        text = txt_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text = txt_bytes.decode('cp949')
    return parse_script_text(text)


# --- 텍스트를 문단/단어 토큰으로 분해 (**강조** 표시 인식) ---
def tokenize(text):
    # 글자마다 강조 여부를 먼저 매긴다 (줄바꿈을 넘어가는 강조도 인식)
    chars, pos = [], 0
    for m in EMPH_RE.finditer(text):
        chars += [(c, False) for c in text[pos:m.start()]]
        chars += [(c, True) for c in (m.group(1) or m.group(2))]
        pos = m.end()
    chars += [(c, False) for c in text[pos:]]

    # 줄바꿈으로 문단을, 공백으로 단어를 나눈다
    paragraphs, words, cur, emph = [], [], "", False

    def flush_word():
        nonlocal cur, emph
        if cur:
            words.append([cur, emph])
            cur, emph = "", False

    for c, e in chars:
        if c == "\n":
            flush_word()
            paragraphs.append(words)
            words = []
        elif c.isspace():
            flush_word()
        else:
            cur += c
            emph = emph or e          # 단어 일부만 강조돼도 그 단어 전체를 강조
    flush_word()
    paragraphs.append(words)
    return paragraphs


# --- 지정한 사각형에 들어갈 만큼만 그리고, 남은 위치를 반환 ---
def fill_box(page, rect, tokens, state, font, size):
    """state = [para_idx, word_idx]. 반환: 소비 후 state, 그린 줄 수"""
    lh = size * LINE_RATIO
    max_lines = int(rect.height // lh)
    if max_lines <= 0 or rect.width <= size:
        return state, 0

    pi, wi = state
    width = rect.width
    lines = []

    while pi < len(tokens) and len(lines) < max_lines:
        words = tokens[pi]
        if not words:                       # 빈 줄(문단 구분)
            lines.append("")
            pi += 1
            wi = 0
            continue

        cur, cur_w = [], 0.0
        space_w = font.width(" ", size)
        while wi < len(words):
            w, w_emph = words[wi]
            w_w = font.width(w, size)
            add_w = w_w if not cur else space_w + w_w
            if cur_w + add_w <= width:
                cur.append((w, w_emph))
                cur_w += add_w
                wi += 1
            else:
                if not cur:                 # 한 단어가 통째로 너무 길면 글자 단위로 자름
                    k, acc = 0, 0.0
                    while k < len(w):
                        cw = font.width(w[k], size)
                        if acc + cw > width:
                            break
                        acc += cw
                        k += 1
                    k = max(1, k)
                    cur.append((w[:k], w_emph))
                    words[wi] = [w[k:], w_emph]   # 나머지는 다음 줄로
                break

        lines.append(cur)
        if wi >= len(words):
            pi += 1
            wi = 0

    y = rect.y0 + size
    for ln in lines:
        if ln:
            # 강조 여부가 같은 단어끼리 묶어서 (띄어쓰기 포함) 한 덩어리로 그림
            groups = []
            for w, e in ln:
                if groups and groups[-1][1] == e:
                    groups[-1][0] += " " + w
                else:
                    groups.append([w, e])

            x = rect.x0
            for gi, (text, emph) in enumerate(groups):
                if gi:
                    x += space_w
                gw = font.width(text, size)
                if emph:                      # 노란 형광펜
                    page.draw_rect(fitz.Rect(x - 0.5, y - size * 0.92, x + gw + 0.5, y + size * 0.26),
                                   color=None, fill=EMPH_BG)
                for run, use_fb in font.runs(text):
                    page.insert_text(fitz.Point(x, y), run, fontsize=size,
                                     fontname=FB_NAME if use_fb else FONT_NAME,
                                     color=EMPH_COLOR if emph else TEXT_COLOR)
                    x += font.width(run, size)
        y += lh

    return [pi, wi], len(lines)


def is_done(tokens, state):
    pi, wi = state
    if pi >= len(tokens):
        return True
    if pi == len(tokens) - 1 and wi >= len(tokens[pi]):
        return True
    return False


# --- 슬라이드를 box 안에 비율 유지하며 가운데 배치 ---
def place_slide(out_page, box, doc, pno, rot, disp_w, disp_h):
    scale = min(box.width / disp_w, box.height / disp_h)
    sw, sh = disp_w * scale, disp_h * scale
    x0 = box.x0 + (box.width - sw) / 2
    y0 = box.y0 + (box.height - sh) / 2
    out_page.show_pdf_page(fitz.Rect(x0, y0, x0 + sw, y0 + sh), doc, pno, rotate=rot)


# --- 메인 PDF 처리 ---
def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text,
                            margin_cm=8.0, font_size=9.5, margin_side="오른쪽",
                            font_label="Pretendard", max_extra_pages=12):
    MARGIN_PT = margin_cm * CM_TO_PT
    PAD = 12
    FINAL_W = A4_W + MARGIN_PT
    FINAL_H = A4_H

    # 여백 위치에 따라 슬라이드 영역 / 대본 영역 결정
    if margin_side == "왼쪽":
        slide_box = fitz.Rect(MARGIN_PT, 0, FINAL_W, FINAL_H)
        text_box = fitz.Rect(PAD, 15, MARGIN_PT - PAD, FINAL_H - 15)
        divider_x = MARGIN_PT
    else:
        slide_box = fitz.Rect(0, 0, A4_W, FINAL_H)
        text_box = fitz.Rect(A4_W + PAD, 15, FINAL_W - PAD, FINAL_H - 15)
        divider_x = A4_W

    progress_bar.progress(0, text="글씨체 준비 중... (0%)")
    font_path = get_korean_font(font_label)
    fb_path = get_korean_font(FALLBACK_LABEL)
    measure_font = TextMeasurer(font_path, fb_path)

    progress_bar.progress(0, text="PDF 여는 중... (0%)")

    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
    total_pages = len(doc)

    for pno in range(total_pages):
        page = doc[pno]

        # 회전 처리: /Rotate 를 지우고 show_pdf_page 에서 한 번만 적용
        # (/Rotate 는 시계 방향, show_pdf_page 의 rotate 는 반시계 방향이라 부호를 뒤집음)
        src_rot = page.rotation
        rot = (360 - src_rot) % 360
        page.set_rotation(0)
        raw = page.rect
        if src_rot in (90, 270):
            disp_w, disp_h = raw.height, raw.width
        else:
            disp_w, disp_h = raw.width, raw.height

        # ---------- 기본 페이지: A4 가로로 통일한 슬라이드 + 여백 ----------
        p1 = out_doc.new_page(width=FINAL_W, height=FINAL_H)
        p1.draw_rect(p1.rect, color=(1, 1, 1), fill=(1, 1, 1))
        place_slide(p1, slide_box, doc, pno, rot, disp_w, disp_h)
        p1.draw_line(fitz.Point(divider_x, 0), fitz.Point(divider_x, FINAL_H),
                     color=(0.8, 0.8, 0.8), width=0.5)

        script_text = script_dict.get(pno + 1, "").strip()
        if script_text:
            p1.insert_font(fontname=FONT_NAME, fontfile=font_path)
            p1.insert_font(fontname=FB_NAME, fontfile=fb_path)
            tokens = tokenize(script_text)
            state = [0, 0]
            state, _ = fill_box(p1, text_box, tokens, state, measure_font, font_size)

            # ---------- 넘치면: 왼쪽 상단 1/4 축소 슬라이드 + ㄱ자 영역에 이어쓰기 ----------
            mw, mh = FINAL_W / 2, FINAL_H / 2
            mini_box = fitz.Rect(0, 0, mw, mh)
            extra = 0
            while not is_done(tokens, state) and extra < max_extra_pages:
                pe = out_doc.new_page(width=FINAL_W, height=FINAL_H)
                pe.draw_rect(pe.rect, color=(1, 1, 1), fill=(1, 1, 1))
                pe.insert_font(fontname=FONT_NAME, fontfile=font_path)
                pe.insert_font(fontname=FB_NAME, fontfile=fb_path)

                place_slide(pe, mini_box, doc, pno, rot, disp_w, disp_h)
                pe.draw_rect(mini_box, color=(0.8, 0.8, 0.8), width=0.5)

                before = list(state)

                # ① 축소 슬라이드 오른쪽
                box_a = fitz.Rect(mw + PAD, 15, FINAL_W - PAD, mh - 5)
                state, _ = fill_box(pe, box_a, tokens, state, measure_font, font_size)

                # ② 그 아래 전체 폭
                if not is_done(tokens, state):
                    box_b = fitz.Rect(PAD, mh + 10, FINAL_W - PAD, FINAL_H - 15)
                    state, _ = fill_box(pe, box_b, tokens, state, measure_font, font_size)

                if state == before:          # 한 줄도 못 넣었으면 무한루프 방지
                    break
                extra += 1

        # 페이지 작업을 전체의 95%로 보고, 나머지 5%는 저장 단계
        pct = int((pno + 1) / total_pages * 95)
        progress_bar.progress(pct / 100,
                              text=f"PDF 변환 중... {pct}% ({pno + 1} / {total_pages} 페이지)")

    progress_bar.progress(0.96, text="파일 저장 중... 96% (용량이 크면 조금 걸려요)")
    out_doc.subset_fonts()                   # 실제 쓴 글자만 남겨 용량 줄이기
    out_bytes = out_doc.write(garbage=3, deflate=True)
    progress_bar.progress(1.0, text="완료! 100%")
    doc.close()
    out_doc.close()
    return out_bytes


# --- 구글 드라이브 (Apps Script: 폴더 목록 / 폴더 만들기 / 업로드 주소 발급) ---
def get_drive_config():
    try:
        cfg = st.secrets["drive"]
        return cfg["web_app_url"], cfg["secret"]
    except Exception:
        return None


def drive_call(payload, timeout=60):
    web_app_url, secret = get_drive_config()
    r = requests.post(web_app_url, json={"secret": secret, **payload}, timeout=timeout)
    try:
        info = r.json()
    except ValueError:
        raise RuntimeError("Apps Script 응답을 읽지 못했습니다. 웹 앱 배포 시 "
                           "'액세스 권한: 모든 사용자'로 했는지, 새 버전으로 배포했는지 확인해 주세요.")
    if not info.get("ok"):
        raise RuntimeError(info.get("error", "알 수 없는 오류"))
    return info


def list_drive_folders():
    """반환: (최상위 폴더 {id,name}, 바로 아래 폴더 목록 [{id,name}, ...])"""
    info = drive_call({"action": "list"})
    return info["root"], info["folders"]


def create_drive_folder(name):
    return drive_call({"action": "mkdir", "name": name})


def upload_to_drive(pdf_bytes, file_name, progress_bar, folder_id=None, chunk_mb=8):
    total = len(pdf_bytes)

    # ① Apps Script에 업로드 주소 요청
    progress_bar.progress(0, text="드라이브 업로드 준비 중... 0%")
    info = drive_call({"action": "upload", "name": file_name, "size": total, "folderId": folder_id})
    upload_url = info["uploadUrl"]
    folder_name = info.get("folderName", "")

    # ② 파일을 조각내서 드라이브로 직접 전송 (조각 크기는 256KB의 배수여야 함)
    chunk = chunk_mb * 1024 * 1024
    start, retries = 0, 0
    while True:
        end = min(start + chunk, total) - 1
        try:
            resp = requests.put(
                upload_url, data=pdf_bytes[start:end + 1],
                headers={"Content-Range": f"bytes {start}-{end}/{total}"},
                allow_redirects=False, timeout=300)
        except requests.RequestException:
            resp = None

        if resp is not None and resp.status_code in (200, 201):
            progress_bar.progress(1.0, text="드라이브 업로드 완료! 100%")
            return f"https://drive.google.com/file/d/{resp.json().get('id')}/view", folder_name

        if resp is not None and resp.status_code == 308:      # 이어서 보낼 것
            rng = resp.headers.get("Range")                    # 예: bytes=0-8388607
            start = int(rng.split("-")[1]) + 1 if rng else 0
            retries = 0
            pct = int(start / total * 100)
            progress_bar.progress(min(pct, 99) / 100,
                                  text=f"드라이브 업로드 중... {pct}% "
                                       f"({start / 1e6:.1f} / {total / 1e6:.1f} MB)")
            continue

        # 일시 오류(네트워크, 5xx) → 서버가 받은 위치를 확인하고 최대 3번 재시도
        retries += 1
        if retries > 3 or (resp is not None and resp.status_code < 500):
            detail = f"({resp.status_code}) {resp.text[:200]}" if resp is not None else "네트워크 오류"
            raise RuntimeError(f"업로드 실패 {detail}")
        chk = requests.put(upload_url, headers={"Content-Range": f"bytes */{total}"},
                           allow_redirects=False, timeout=60)
        if chk.status_code in (200, 201):
            progress_bar.progress(1.0, text="드라이브 업로드 완료! 100%")
            return f"https://drive.google.com/file/d/{chk.json().get('id')}/view", folder_name
        rng = chk.headers.get("Range")
        start = int(rng.split("-")[1]) + 1 if rng else 0


# --- Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기")
st.markdown("슬라이드를 A4 가로 크기로 통일한 뒤 여백을 만들고 대본을 넣습니다. 대본이 길면 글씨를 줄이지 않고 "
            "**축소 슬라이드가 붙은 이어쓰기 페이지**를 추가합니다.  \n"
            "대본에 `**이렇게**` 또는 `==이렇게==` 표시한 부분은 "
            "<span style='background:#FFEE59;color:#CC0D0D'>빨간 글씨 + 노란 형광펜</span>으로 나옵니다.",
            unsafe_allow_html=True)
st.write("---")

st.subheader("1️⃣ PDF 파일 업로드")
uploaded_pdf = st.file_uploader("변환할 PDF 파일을 올려주세요", type=["pdf"])

st.subheader("2️⃣ 강의 대본 입력 (선택)")
tab1, tab2 = st.tabs(["📋 텍스트 직접 붙여넣기", "📄 TXT 파일 업로드"])

with tab1:
    pasted_text = st.text_area(
        "클로바노트 등에서 복사한 대본을 여기에 붙여넣으세요.",
        height=200,
        placeholder="형식 예시:\n[1페이지] 첫 번째 슬라이드 내용입니다.\n[2페이지] **이 문장은 강조됩니다** 나머지는 보통 글씨...\n\n※ 빈칸으로 두면 여백만 생성됩니다."
    )

with tab2:
    uploaded_txt = st.file_uploader("또는 TXT 대본 파일을 업로드하세요", type=["txt"])

st.subheader("3️⃣ 설정")
margin_side = st.radio("여백 위치", ["오른쪽", "왼쪽"], horizontal=True)
font_label = st.selectbox("글씨체", list(FONTS.keys()),
                          help="선택한 글씨체에 없는 글자(ç, α 등)는 자동으로 Pretendard로 표시됩니다.")
col1, col2 = st.columns(2)
with col1:
    margin_cm = st.slider("여백 크기 (cm)", 4.0, 12.0, 8.0, 0.5)
with col2:
    font_size = st.slider("대본 글자 크기 (pt)", 6.0, 14.0, 9.5, 0.5)

if uploaded_pdf is not None:
    # 새 PDF를 올리면 이전 결과 초기화
    if st.session_state.get("src_name") != uploaded_pdf.name:
        st.session_state["src_name"] = uploaded_pdf.name
        st.session_state.pop("output_bytes", None)

    st.write("---")
    if st.button("✨ PDF 변환 및 대본 매칭 실행", type="primary", use_container_width=True):
        progress_bar = st.progress(0, text="준비 중... 0%")
        status_text = st.empty()
        try:
            script_dict = {}
            if uploaded_txt is not None:
                script_dict = parse_script_bytes(uploaded_txt.getvalue())
            elif pasted_text.strip():
                script_dict = parse_script_text(pasted_text)

            st.session_state.pop("output_bytes", None)
            st.session_state["run_id"] = st.session_state.get("run_id", 0) + 1
            st.session_state["output_bytes"] = process_pdf_with_script(
                uploaded_pdf.getvalue(), script_dict, progress_bar, status_text,
                margin_cm, font_size, margin_side, font_label
            )
            status_text.success("🎉 모든 작업 완료! 아래에서 파일 이름을 정하고 다운로드하세요.")
            st.balloons()
        except Exception as e:
            st.error(f"오류가 발생했습니다: {e}")

    # 결과는 session_state에 저장 → 파일명을 고쳐도 결과가 사라지지 않음
    if "output_bytes" in st.session_state:
        st.subheader("4️⃣ 다운로드")
        default_name = f"대본추가_{os.path.splitext(uploaded_pdf.name)[0]}.pdf"
        # 변환할 때마다 새 입력칸 → 항상 '대본추가_원본이름.pdf'가 채워진 상태로 시작
        out_name = st.text_input("저장할 파일 이름 (수정 가능)", value=default_name,
                                 key=f"out_name_{st.session_state.get('run_id', 0)}").strip()
        out_name = re.sub(r'[\\/:*?"<>|]', "_", out_name) or "대본추가"
        if not out_name.lower().endswith(".pdf"):
            out_name += ".pdf"
        st.download_button(
            label=f"📥 {out_name} 다운로드",
            data=st.session_state["output_bytes"],
            file_name=out_name,
            mime="application/pdf",
            use_container_width=True
        )

        # --- 구글 드라이브 업로드 ---
        if get_drive_config() is None:
            st.caption("📤 구글 드라이브 업로드를 쓰려면 앱 설정(Secrets)에 [drive] 항목을 넣어주세요.")
        else:
            st.markdown("**📤 구글 드라이브에 저장**")

            # 폴더 목록 불러오기 (한 번 불러오면 🔄 누르기 전까지 재사용)
            if "drive_folders" not in st.session_state:
                try:
                    with st.spinner("드라이브 폴더 목록 불러오는 중..."):
                        st.session_state["drive_folders"] = list_drive_folders()
                except Exception as e:
                    st.error(f"폴더 목록을 불러오지 못했습니다: {e}")

            if "drive_folders" in st.session_state:
                root, children = st.session_state["drive_folders"]
                labels = {root["id"]: f"{root['name']} (바로 여기에)"}
                for f in children:
                    labels[f["id"]] = f"{root['name']} / {f['name']}"
                ids = list(labels.keys())

                # 방금 만든 폴더 자동 선택 / 없어진 폴더면 최상위로
                if "drive_pending_sel" in st.session_state:
                    st.session_state["drive_folder_sel"] = st.session_state.pop("drive_pending_sel")
                if st.session_state.get("drive_folder_sel") not in ids:
                    st.session_state["drive_folder_sel"] = root["id"]

                c1, c2 = st.columns([5, 1])
                with c1:
                    folder_id = st.selectbox("📁 저장할 폴더", ids, key="drive_folder_sel",
                                             format_func=lambda i: labels[i])
                with c2:
                    st.write("")
                    st.write("")
                    if st.button("🔄", help="드라이브에서 폴더 목록 다시 불러오기",
                                 use_container_width=True):
                        st.session_state.pop("drive_folders", None)
                        st.rerun()

                with st.expander(f"➕ 새 폴더 만들기 ({root['name']} 안에)"):
                    nk = st.session_state.get("drive_new_folder_n", 0)
                    new_name = st.text_input("새 폴더 이름", key=f"drive_new_folder_{nk}",
                                             placeholder="예: 소화기학").strip()
                    if st.button("폴더 만들기", disabled=not new_name):
                        try:
                            res = create_drive_folder(new_name)
                            st.session_state["drive_folders"] = list_drive_folders()
                            st.session_state["drive_pending_sel"] = res["folder"]["id"]
                            st.session_state["drive_new_folder_n"] = nk + 1
                            st.session_state["drive_msg"] = (
                                f"이미 있는 '{new_name}' 폴더를 선택했습니다." if res.get("existed")
                                else f"'{new_name}' 폴더를 만들고 선택했습니다.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"폴더를 만들지 못했습니다: {e}")
                if "drive_msg" in st.session_state:
                    st.info(st.session_state.pop("drive_msg"))

                up_key = f"drive_link_{st.session_state.get('run_id', 0)}_{folder_id}_{out_name}"
                if st.button(f"📤 '{labels[folder_id]}'에 업로드", use_container_width=True):
                    up_bar = st.progress(0, text="드라이브 업로드 준비 중... 0%")
                    try:
                        st.session_state[up_key] = upload_to_drive(
                            st.session_state["output_bytes"], out_name, up_bar, folder_id)
                    except Exception as e:
                        st.error(f"드라이브 업로드 실패: {e}")
                if up_key in st.session_state:
                    link, folder_name = st.session_state[up_key]
                    where = f"'{folder_name}' 폴더에 " if folder_name else ""
                    st.success(f"✅ {where}**{out_name}** 업로드 완료 → [드라이브에서 열기]({link})")
