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

# --- 대본 텍스트 파싱 함수 (인식률 대폭 강화) ---
def parse_script_text(text):
    # 괄호 유무, 띄어쓰기 등 다양한 형태를 모두 유연하게 인식합니다.
    pattern = r'\[?(\d+)\s*페이지\]?[-:\s]*(.*?)(?=\[?\d+\s*페이지\]?[-:\s]*|$)'
    matches = re.findall(pattern, text, re.DOTALL)
    
    script_dict = {}
    for match in matches:
        page_num = int(match[0])
        content = match[1].strip()
        if content: # 내용이 비어있지 않은 경우만 저장
            script_dict[page_num] = content
            
    return script_dict

def parse_script_bytes(txt_bytes):
    try:
        text = txt_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text = txt_bytes.decode('cp949')
    return parse_script_text(text)

# --- 메인 PDF 처리 함수 ---
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
        
        # 1. 방향 인식 및 스케일링
        if orig_width > orig_height:
            base_w, base_h = 29.7 * CM_TO_PT, 21.0 * CM_TO_PT
        else:
            base_w, base_h = 21.0 * CM_TO_PT, 29.7 * CM_TO_PT
            
        scale = min(base_w / orig_width, base_h / orig_height)
        scaled_width, scaled_height = orig_width * scale, orig_height * scale
        
        dx = (base_w - scaled_width) / 2
        dy = (base_h - scaled_height) / 2
        
        FINAL_WIDTH = base_w + MARGIN_PT
        FINAL_HEIGHT = base_h
        
        target_rect = fitz.Rect(dx, dy, dx + scaled_width, dy + scaled_height)
        out_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
        out_page.show_pdf_page(target_rect, doc, pno)
        
        # --- ✨ 디자인 포인트: 여백 구분용 회색 점선 그리기 ---
        out_page.draw_line(
            fitz.Point(base_w, 20), 
            fitz.Point(base_w, FINAL_HEIGHT - 20), 
            color=(0.7, 0.7, 0.7), 
            width=1.0,
            dashes="[3 3]" # 점선 스타일
        )
        
        # 2. 텍스트 삽입 로직
        current_page_num = pno + 1
        if current_page_num in script_dict:
            script_text = script_dict[current_page_num]
            
            text_rect = fitz.Rect(
                base_w + 10,       # x0 (슬라이드 끝 + 10pt)
                20,                # y0
                FINAL_WIDTH - 10,  # x1
                FINAL_HEIGHT - 20  # y1
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

st.subheader("1️⃣ PDF 파일 업로드")
uploaded_pdf = st.file_uploader("변환할 PDF 파일을 올려주세요", type=["pdf"])

st.subheader("2️⃣ 강의 대본 입력 (선택)")
tab1, tab2 = st.tabs(["📋 텍스트 직접 붙여넣기", "📄 TXT 파일 업로드"])

with tab1:
    pasted_text = st.text_area(
        "대본을 붙여넣으세요. (1페이지 내용 ... 양식으로 적으시면 됩니다)", 
        height=200
    )
with tab2:
    uploaded_txt = st.file_uploader("또는 TXT 대본 파일을 업로드하세요", type=["txt"])

# 실시간 대본 인식 상태 보여주기
script_dict = {}
if uploaded_txt is not None:
    script_dict = parse_script_bytes(uploaded_txt.read())
elif pasted_text.strip():
    script_dict = parse_script_text(pasted_text)

if script_dict:
    recognized_pages = ", ".join([str(k) for k in sorted(script_dict.keys())])
    st.success(f"✅ 총 {len(script_dict)}개의 대본이 성공적으로 인식되었습니다! (인식된 페이지: {recognized_pages})")
elif pasted_text.strip() or uploaded_txt is not None:
    st.warning("⚠️ 텍스트를 입력하셨지만, '1페이지' 형태의 번호를 찾지 못했습니다. 번호 양식을 다시 확인해 주세요.")

if uploaded_pdf is not None:
    st.write("---")
    if st.button("✨ PDF 변환 실행하기", type="primary", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            input_bytes = uploaded_pdf.read()
            output_bytes = process_pdf_with_script(input_bytes, script_dict, progress_bar, status_text)
            
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
