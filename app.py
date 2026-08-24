import streamlit as st
import fitz  # PyMuPDF
import re
import os
import urllib.request

# --- 한글 폰트 자동 다운로드 함수 ---
@st.cache_resource
def get_korean_font():
    font_path = "NanumGothic.ttf"
    if not os.path.exists(font_path):
        url = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
        urllib.request.urlretrieve(url, font_path)
    return font_path

# --- 대본 텍스트 파싱 함수 ---
def parse_script_text(text):
    pattern = r'\[(\d+)페이지\](.*?)(?=\[\d+페이지\]|$)'
    matches = re.findall(pattern, text, re.DOTALL)
    script_dict = {}
    for page_num_str, content in matches:
        script_dict[int(page_num_str)] = content.strip()
    return script_dict

def parse_script_bytes(txt_bytes):
    try:
        text = txt_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text = txt_bytes.decode('cp949')
    return parse_script_text(text)

# --- 메인 PDF 처리 함수 (방향 자동 인식 적용) ---
def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text):
    CM_TO_PT = 28.3465
    MARGIN_PT = 8.0 * CM_TO_PT
    
    font_path = get_korean_font()
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
    total_pages = len(doc)
    
    for pno in range(total_pages):
        page = doc[pno]
        rect = page.rect
        orig_width, orig_height = rect.width, rect.height
        
        # 1. 원본 방향 감지하여 A4 크기 유동적 설정 (핵심 변경점)
        if orig_width > orig_height:
            # 가로형 슬라이드 (가로 29.7cm, 세로 21cm)
            base_w = 29.7 * CM_TO_PT
            base_h = 21.0 * CM_TO_PT
        else:
            # 세로형 문서 (가로 21cm, 세로 29.7cm)
            base_w = 21.0 * CM_TO_PT
            base_h = 29.7 * CM_TO_PT
            
        # 2. 비율 유지하며 화면에 꽉 차게 스케일링
        scale = min(base_w / orig_width, base_h / orig_height)
        scaled_width, scaled_height = orig_width * scale, orig_height * scale
        
        dx = (base_w - scaled_width) / 2
        dy = (base_h - scaled_height) / 2
        
        # 3. 최종 도화지 크기 설정 (기본 종이 크기 + 오른쪽 8cm 대본 여백)
        FINAL_WIDTH = base_w + MARGIN_PT
        FINAL_HEIGHT = base_h
        
        target_rect = fitz.Rect(dx, dy, dx + scaled_width, dy + scaled_height)
        out_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
        out_page.show_pdf_page(target_rect, doc, pno)
        
        # 4. 텍스트 삽입 로직
        current_page_num = pno + 1
        if current_page_num in script_dict:
            script_text = script_dict[current_page_num]
            
            if script_text:
                # 대본이 들어갈 위치 (기준 종이 가로 끝나는 지점부터 시작)
                text_rect = fitz.Rect(
                    base_w + 10,       # x0 (슬라이드 끝 + 10pt 여백)
                    20,                # y0 (위에서 20pt)
                    FINAL_WIDTH - 10,  # x1 (전체 종이 끝 - 10pt)
                    FINAL_HEIGHT - 20  # y1 (아래에서 20pt)
                )
                
                out_page.insert_font(fontname="nanum", fontfile=font_path)
                out_page.insert_textbox(
                    text_rect, 
                    script_text, 
                    fontsize=11,       # 태블릿 가독성을 위해 글씨 크기 10 -> 11로 상향
                    fontname="nanum", 
                    align=fitz.TEXT_ALIGN_LEFT
                )
        
        progress = (pno + 1) / total_pages
        progress_bar.progress(progress)
        status_text.caption(f"대본 매칭 중... 📝 ({pno + 1} / {total_pages} 페이지 완료)")
        
    out_bytes = out_doc.write()
    doc.close()
    out_doc.close()
    return out_bytes

# --- 🎨 Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기")
st.markdown("PDF를 업로드하고 대본을 입력하면, 지정된 페이지 옆 여백에 텍스트를 자동으로 쏙 넣어줍니다.")
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

if uploaded_pdf is not None:
    st.write("---")
    if st.button("✨ PDF 변환 및 대본 매칭 실행", type="primary", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            script_dict = {}
            if uploaded_txt is not None:
                txt_bytes = uploaded_txt.read()
                script_dict = parse_script_bytes(txt_bytes)
            elif pasted_text.strip():
                script_dict = parse_script_text(pasted_text)
            
            input_bytes = uploaded_pdf.read()
            output_bytes = process_pdf_with_script(input_bytes, script_dict, progress_bar, status_text)
            
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
