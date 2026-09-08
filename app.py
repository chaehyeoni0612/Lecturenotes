import streamlit as st
try:
    import pymupdf as fitz          # PyMuPDF 1.24.3+
except ImportError:
    import fitz                     # 구버전 호환
import re
import os
import urllib.request

# --- 한글 폰트 자동 다운로드 ---
@st.cache_resource
def get_korean_font():
    font_path = "NanumGothic-Regular.ttf"
    if not os.path.exists(font_path):
        url = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
        urllib.request.urlretrieve(url, font_path)
    return font_path


# --- 대본 텍스트 파싱 ---
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


# --- 여백 안에 대본 넣기 (넘치면 자동으로 글씨 줄임) ---
def fit_textbox(page, rect, text, fontname, start_size=10.0, min_size=4.5):
    size = start_size
    while size >= min_size:
        rc = page.insert_textbox(
            rect, text, fontsize=size, fontname=fontname,
            align=fitz.TEXT_ALIGN_LEFT, lineheight=1.25
        )
        if rc >= 0:          # 0 이상이면 다 들어감
            return size
        size -= 0.5
    # 최소 크기로 한 번 더 (잘려도 넣음)
    page.insert_textbox(rect, text, fontsize=min_size, fontname=fontname,
                        align=fitz.TEXT_ALIGN_LEFT, lineheight=1.25)
    return min_size


# --- 메인 PDF 처리 ---
def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text,
                            margin_cm=8.0):
    CM_TO_PT = 28.3465
    MARGIN_PT = margin_cm * CM_TO_PT

    font_path = get_korean_font()
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
    total_pages = len(doc)

    for pno in range(total_pages):
        page = doc[pno]

        # ★ 핵심 수정 ★
        # /Rotate 값을 먼저 0으로 없애고(= 원본 좌표계로 되돌리고),
        # 그 회전값을 show_pdf_page에 '직접' 한 번만 적용한다.
        # (PyMuPDF 버전에 따라 show_pdf_page가 /Rotate를 자동 반영하기도 하고
        #  안 하기도 해서, 그대로 두면 회전이 두 번 먹혀 180도 뒤집힘)
        rot = page.rotation
        page.set_rotation(0)
        raw = page.rect

        if rot in (90, 270):
            w, h = raw.height, raw.width      # 화면에 보이는 가로/세로
        else:
            w, h = raw.width, raw.height

        FINAL_WIDTH = w + MARGIN_PT
        FINAL_HEIGHT = h

        out_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
        out_page.draw_rect(out_page.rect, color=(1, 1, 1), fill=(1, 1, 1))

        # 슬라이드를 왼쪽에 정방향으로 배치
        out_page.show_pdf_page(fitz.Rect(0, 0, w, h), doc, pno, rotate=rot)

        # 슬라이드와 여백 경계선 (필요 없으면 이 줄 삭제)
        out_page.draw_line(fitz.Point(w, 0), fitz.Point(w, h),
                           color=(0.8, 0.8, 0.8), width=0.5)

        script_text = script_dict.get(pno + 1, "").strip()
        if script_text:
            out_page.insert_font(fontname="kfont", fontfile=font_path)
            text_rect = fitz.Rect(w + 12, 15, FINAL_WIDTH - 12, FINAL_HEIGHT - 15)
            fit_textbox(out_page, text_rect, script_text, "kfont")

        progress_bar.progress((pno + 1) / total_pages)
        status_text.caption(f"PDF 변환 중... 📝 ({pno + 1} / {total_pages} 페이지)")

    out_bytes = out_doc.write()
    doc.close()
    out_doc.close()
    return out_bytes


# --- Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기")
st.markdown("PDF를 업로드하고 대본을 입력하면, 각 슬라이드 우측에 여백과 대본이 생성됩니다.")
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

margin_cm = st.slider("오른쪽 여백 크기 (cm)", 4.0, 12.0, 8.0, 0.5)

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
                uploaded_pdf.read(), script_dict, progress_bar, status_text, margin_cm
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
