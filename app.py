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
    pattern = r'\[?(\d+)\s*페이지\]?[-:\s]*(.*?)(?=\[?\d+\s*페이지\]?[-:\s]*|$)'
    matches = re.findall(pattern, text, re.DOTALL)
    
    script_dict = {}
    for match in matches:
        page_num = int(match[0])
        content = match[1].strip()
        if content:
            script_dict[page_num] = content
    return script_dict

def parse_script_bytes(txt_bytes):
    try:
        text = txt_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text = txt_bytes.decode('cp949')
    return parse_script_text(text)

# --- 메인 PDF 처리 함수 ---
def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text, is_landscape):
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
        
        # --- ✨ 사용자가 선택한 방향으로 강제 고정 ---
        if is_landscape:
            base_w, base_h = 29.7 * CM_TO_PT, 21.0 * CM_TO_PT  # 가로형
        else:
            base_w, base_h = 21.0 * CM_TO_PT, 29.7 * CM_TO_PT  # 세로형
            
        scale = min(base_w / orig_width, base_h / orig_height)
        scaled_width, scaled_height = orig_width * scale, orig_height * scale
        
        dx = (base_w - scaled_width) / 2
        dy = (base_h - scaled_height) / 2
        
        FINAL_WIDTH = base_w + MARGIN_PT
        FINAL_HEIGHT = base_h
        
        target_rect = fitz.Rect(dx, dy, dx + scaled_width, dy + scaled_height)
        out_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
        out_page.show_pdf_page(target_rect, doc, pno)
        
        # 노트 필기용 회색 점선
        out_page.draw_line(
            fitz.Point(base_w, 20), 
            fitz.Point(base_w, FINAL_HEIGHT - 20), 
            color=(0.7, 0.7, 0.7), 
            width=1.0,
            dashes="[3 3]"
        )
        
        # 텍스트 삽입
        current_page_num = pno + 1
        if current_page_num in script_dict:
            script_text = script_dict[current_page_num]
            
            text_rect = fitz.Rect(
                base_w + 10,       
                20,                
                FINAL_WIDTH - 10,  
                FINAL_HEIGHT - 20  
            )
            
            out_page.insert_font(fontname="nanum", fontfile=font_path)
            out_page.insert_textbox(
                text_rect, 
                script_text, 
                fontsize=11, 
                fontname="nanum", 
                align=fitz.TEXT_ALIGN_LEFT
            )
        
        progress = (pno + 1) / total_pages
        progress_bar.progress(progress)
        status_text.caption(f"문서 처리 중... 📝 ({pno + 1} / {total_pages} 페이지 완료)")
        
    out_bytes = out_doc.write()
    doc.close()
    out_doc.close()
    return out_bytes

# --- 🎨 Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기")
st.write("---")

# ✨ 추가된 옵션: 용지 방향 직접 선택
st.subheader("⚙️ 1. 용지 방향 선택")
st.info("💡 강의록(PPT)은 가로형, 일반 문서나 논문은 세로형을 선택하세요.")
orientation = st.radio(
    "출력될 PDF의 기본 형태를 고르세요:",
    ["가로형 (슬라이드 꽉 차게)", "세로형 (일반 문서)"],
    horizontal=True
)
is_landscape = (orientation == "가로형 (슬라이드 꽉 차게)")
st.write("---")

st.subheader("📄 2. PDF 파일 업로드")
uploaded_pdf = st.file_uploader("변환할 PDF 파일을 올려주세요", type=["pdf"])

st.subheader("📝 3. 강의 대본 입력 (선택)")
tab1, tab2 = st.tabs(["📋 텍스트 직접 붙여넣기", "📄 TXT 파일 업로드"])

with tab1:
    pasted_text = st.text_area(
        "대본을 붙여넣으세요. (1페이지 내용 ... 양식)", 
        height=200
    )
with tab2:
    uploaded_txt = st.file_uploader("또는 TXT 대본 파일을 업로드하세요", type=["txt"])

script_dict = {}
if uploaded_txt is not None:
    script_dict = parse_script_bytes(uploaded_txt.read())
elif pasted_text.strip():
    script_dict = parse_script_text(pasted_text)

if script_dict:
    st.success(f"✅ 총 {len(script_dict)}개의 대본이 성공적으로 인식되었습니다!")

if uploaded_pdf is not None:
    st.write("---")
    if st.button("✨ PDF 변환 실행하기", type="primary", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            input_bytes = uploaded_pdf.read()
            # is_landscape 값을 함수로 넘겨주어 방향을 강제합니다.
            output_bytes = process_pdf_with_script(input_bytes, script_dict, progress_bar, status_text, is_landscape)
            
            status_text.text("🎉 모든 작업 완료!")
            st.balloons()
            
            st.download_button(
                label="📥 변환된 PDF 다운로드",
                data=output_bytes,
                file_name=f"노트변환_{uploaded_pdf.name}",
                mime="application/pdf",
                use_container_width=True
            )
        except Exception as e:
            st.error(f"오류가 발생했습니다: {e}")
