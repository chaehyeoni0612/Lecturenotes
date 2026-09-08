import streamlit as st
import fitz  # PyMuPDF
import re
import os
import urllib.request

# --- 맑은 고딕 폰트 자동 다운로드 함수 ---
@st.cache_resource
def get_korean_font():
    font_path = "malgun.ttf"
    if not os.path.exists(font_path):
        url = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
        urllib.request.urlretrieve(url, font_path)
    return font_path

# --- 대본 텍스트 파싱 함수 ---
def parse_script_text(text):
    splits = re.split(r'\[(\d+)페이지\]', text)
    script_dict = {}
    for i in range(1, len(splits), 2):
        if i + 1 < len(splits):
            page_num = int(splits[i])
            content = splits[i+1].strip()
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

# --- 메인 PDF 처리 함수 (회전된 슬라이드 정방향 고정 및 우측 여백 생성) ---
def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text):
    CM_TO_PT = 28.3465
    MARGIN_PT = 8.0 * CM_TO_PT  # 오른쪽 여백 8cm
    
    font_path = get_korean_font()
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
    total_pages = len(doc)
    
    for pno in range(total_pages):
        page = doc[pno]
        rect = page.rect
        rotation = page.rotation
        
        # 💡 핵심 해결: 슬라이드가 누워있든(90, 270도) 서 있든, 사람이 보는 실제 가로/세로 폭을 정확히 계산
        if rotation in [90, 270]:
            w = rect.height
            h = rect.width
        else:
            w = rect.width
            h = rect.height
        
        FINAL_WIDTH = w + MARGIN_PT
        FINAL_HEIGHT = h
        
        # 1. 정방향 규격으로 새 페이지 생성 및 백색 배경 채우기
        out_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
        out_page.draw_rect(out_page.rect, color=(1, 1, 1), fill=(1, 1, 1))
        
        # 2. 눕거나 찌그러진 원본 슬라이드를 회전 상태를 완벽히 반영하여 정방향(왼쪽)에 안착
        target_rect = fitz.Rect(0, 0, w, h)
        out_page.show_pdf_page(target_rect, doc, pno, rotate=int(rotation))
        
        current_page_num = pno + 1
        script_text = script_dict.get(current_page_num, "").strip()
        
        # 3. 대본이 있는 경우 우측 8cm 여백에 맑은 고딕으로 선명하게 삽입
        if script_text:
            out_page.insert_font(fontname="malgun", fontfile=font_path)
            
            text_rect = fitz.Rect(w + 10, 15, FINAL_WIDTH - 10, FINAL_HEIGHT - 15)
            
            text_len = len(script_text)
            if text_len > 1500:
                font_size = 6.0
            elif text_len > 1000:
                font_size = 7.0
            elif text_len > 600:
                font_size = 8.0
            elif text_len > 400:
                font_size = 9.0
            else:
                font_size = 9.5
            
            out_page.insert_textbox(
                text_rect, 
                script_text, 
                fontsize=font_size, 
                fontname="malgun", 
                align=fitz.TEXT_ALIGN_LEFT
            )
        
        progress = (pno + 1) / total_pages
        progress_bar.progress(progress)
        status_text.caption(f"PDF 변환 중... 📝 ({pno + 1} / {total_pages} 페이지 완료)")
        
    out_bytes = out_doc.write()
    doc.close()
    out_doc.close()
    return out_bytes

# --- 🎨 Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기")
st.markdown("PDF를 업로드하고 대본을 입력하면, 각 슬라이드 우측에 8cm 여백과 대본이 생성됩니다.")
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
