import streamlit as st
try:
    import pymupdf as fitz          # PyMuPDF 1.24.3+
except ImportError:
    import fitz                     # 구버전 호환
import re
import os
import urllib.request

FONT_NAME = "kfont"
LINE_RATIO = 1.32                   # 줄간격 배수


# --- 한글 폰트 자동 다운로드 ---
@st.cache_resource
def get_korean_font():
    font_path = "NanumGothic-Regular.ttf"
    if not os.path.exists(font_path):
        url = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
        urllib.request.urlretrieve(url, font_path)
    return font_path


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


# --- 텍스트를 문단/단어 토큰으로 분해 ---
def tokenize(text):
    return [para.split() for para in text.split("\n")]


# --- 지정한 사각형에 들어갈 만큼만 그리고, 남은 위치를 반환 ---
def fill_box(page, rect, tokens, state, font, size):
    """state = [para_idx, word_idx]. 반환: 소비 후 state, 그린 줄 수"""
    lh = size * LINE_RATIO
    max_lines = int(rect.height // lh)
    if max_lines <= 0:
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

        cur = ""
        while wi < len(words):
            w = words[wi]
            trial = w if not cur else cur + " " + w
            if font.text_length(trial, size) <= width:
                cur = trial
                wi += 1
            else:
                if not cur:                 # 한 단어가 통째로 너무 길면 글자 단위로 자름
                    k = 1
                    while k <= len(w) and font.text_length(w[:k], size) <= width:
                        k += 1
                    k = max(1, k - 1)
                    cur = w[:k]
                    words[wi] = w[k:]       # 나머지는 다음 줄로
                break

        lines.append(cur)
        if wi >= len(words):
            pi += 1
            wi = 0

    y = rect.y0 + size
    for ln in lines:
        if ln:
            page.insert_text(fitz.Point(rect.x0, y), ln,
                             fontsize=size, fontname=FONT_NAME)
        y += lh

    return [pi, wi], len(lines)


def is_done(tokens, state):
    pi, wi = state
    if pi >= len(tokens):
        return True
    if pi == len(tokens) - 1 and wi >= len(tokens[pi]):
        return True
    return False


# --- 메인 PDF 처리 ---
def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text,
                            margin_cm=8.0, font_size=9.5, max_extra_pages=12):
    CM_TO_PT = 28.3465
    MARGIN_PT = margin_cm * CM_TO_PT
    PAD = 12

    font_path = get_korean_font()
    measure_font = fitz.Font(fontfile=font_path)

    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
    total_pages = len(doc)

    for pno in range(total_pages):
        page = doc[pno]

        # 회전 중복 적용 방지: /Rotate 를 지우고 show_pdf_page 에서 한 번만 적용
        rot = page.rotation
        page.set_rotation(0)
        raw = page.rect
        if rot in (90, 270):
            w, h = raw.height, raw.width
        else:
            w, h = raw.width, raw.height

        FINAL_W = w + MARGIN_PT
        FINAL_H = h

        # ---------- 기본 페이지: 슬라이드 원본 크기 + 우측 여백 ----------
        p1 = out_doc.new_page(width=FINAL_W, height=FINAL_H)
        p1.draw_rect(p1.rect, color=(1, 1, 1), fill=(1, 1, 1))
        p1.show_pdf_page(fitz.Rect(0, 0, w, h), doc, pno, rotate=rot)
        p1.draw_line(fitz.Point(w, 0), fitz.Point(w, h),
                     color=(0.8, 0.8, 0.8), width=0.5)

        script_text = script_dict.get(pno + 1, "").strip()
        if script_text:
            p1.insert_font(fontname=FONT_NAME, fontfile=font_path)
            tokens = tokenize(script_text)
            state = [0, 0]
            box = fitz.Rect(w + PAD, 15, FINAL_W - PAD, FINAL_H - 15)
            state, _ = fill_box(p1, box, tokens, state, measure_font, font_size)

            # ---------- 넘치면: 1/4 축소 슬라이드 + ㄱ자 영역에 이어쓰기 ----------
            extra = 0
            while not is_done(tokens, state) and extra < max_extra_pages:
                pe = out_doc.new_page(width=FINAL_W, height=FINAL_H)
                pe.draw_rect(pe.rect, color=(1, 1, 1), fill=(1, 1, 1))
                pe.insert_font(fontname=FONT_NAME, fontfile=font_path)

                mw, mh = w / 2, h / 2
                pe.show_pdf_page(fitz.Rect(0, 0, mw, mh), doc, pno, rotate=rot)
                pe.draw_rect(fitz.Rect(0, 0, mw, mh),
                             color=(0.8, 0.8, 0.8), width=0.5)

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

        progress_bar.progress((pno + 1) / total_pages)
        status_text.caption(f"PDF 변환 중... 📝 ({pno + 1} / {total_pages} 페이지)")

    out_bytes = out_doc.write()
    doc.close()
    out_doc.close()
    return out_bytes


# --- Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기")
st.markdown("슬라이드 우측에 여백을 만들고 대본을 넣습니다. 대본이 길면 글씨를 줄이지 않고 "
            "**축소 슬라이드가 붙은 이어쓰기 페이지**를 추가합니다.")
st.write("---")

st.subheader("1️⃣ PDF 파일 업로드")
uploaded_pdf = st.file_uploader("변환할 PDF 파일을 올려주세요", type=["pdf"])

st.subheader("2️⃣ 강의 대본 입력 (선택)")
tab1, tab2 = st.tabs(["📋 텍스트 직접 붙여넣기", "📄 TXT 파일 업로드"])

with tab1:
    pasted_text = st.text_area(
        "클로바노트 등에서 복사한 대본을 여기에 붙여넣으세요.",
        height=200,
        placeholder="형식 예시:\n[1페이지] 첫 번째 슬라이드 내용입니다.\n[2페이지] 두 번째 슬라이드 설명...\n\n※ 빈칸으로 두면 여백만 생성됩니다."
    )

with tab2:
    uploaded_txt = st.file_uploader("또는 TXT 대본 파일을 업로드하세요", type=["txt"])

st.subheader("3️⃣ 설정")
col1, col2 = st.columns(2)
with col1:
    margin_cm = st.slider("오른쪽 여백 (cm)", 4.0, 12.0, 8.0, 0.5)
with col2:
    font_size = st.slider("대본 글자 크기 (pt)", 6.0, 14.0, 9.5, 0.5)

if uploaded_pdf is not None:
    st.write("---")
    if st.button("✨ PDF 변환 및 대본 매칭 실행", type="primary", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        try:
            script_dict = {}
            if uploaded_txt is not None:
                script_dict = parse_script_bytes(uploaded_txt.read())
            elif pasted_text.strip():
                script_dict = parse_script_text(pasted_text)

            output_bytes = process_pdf_with_script(
                uploaded_pdf.read(), script_dict, progress_bar, status_text,
                margin_cm, font_size
            )

            status_text.text("🎉 모든 작업 완료!")
            st.balloons()
            st.download_button(
                label="📥 필기용 PDF 다운로드",
                data=output_bytes,
                file_name=f"대본추가_{uploaded_pdf.name}",
                mime="application/pdf",
                use_container_width=True
            )
        except Exception as e:
            st.error(f"오류가 발생했습니다: {e}")
