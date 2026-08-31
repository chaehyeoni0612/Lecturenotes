import streamlit as st
import fitz  # PyMuPDF
import re
import os
import urllib.request

# --- 맑은 고딕 폰트 자동 다운로드 함수 ---
@st.cache_resource
def get_korean_font():
    font_path = "NotoSansKR-Regular.ttf"
    if not os.path.exists(font_path):
        url = "https://github.com/google/fonts/raw/main/ofl/notosanskr/static/NotoSansKR-Regular.ttf"
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

# --- 메인 PDF 처리 함수 ---
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
        orig_width, orig_height = rect.width, rect.height
        
        # 1. 가로/세로 방향 감지
        is_landscape = orig_width > orig_height
        if is_landscape:
            base_w = 29.7 * CM_TO_PT
            base_h = 21.0 * CM_TO_PT
        else:
            base_w = 21.0 * CM_TO_PT
            base_h = 29.7 * CM_TO_PT
            
        scale = min(base_w / orig_width, base_h / orig_height)
        scaled_width, scaled_height = orig_width * scale, orig_height * scale
        
        dx = (base_w - scaled_width) / 2
        dy = (base_h - scaled_height) / 2
        
        FINAL_WIDTH = base_w + MARGIN_PT
        FINAL_HEIGHT = base_h
        
        # --- [1단계] 메인 슬라이드 페이지 생성 ---
        target_rect = fitz.Rect(dx, dy, dx + scaled_width, dy + scaled_height)
        out_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
        out_page.show_pdf_page(target_rect, doc, pno)
        
        current_page_num = pno + 1
        if current_page_num in script_dict:
            script_text = script_dict[current_page_num]
            
            if script_text:
                out_page.insert_font(fontname="malgun", fontfile=font_path)
                
                # --- 💡 바로 이 부분 수치를 중간으로 조정했습니다! ---
                # 가로형(landscape)일 때와 세로형(portrait)일 때의 최대 글자 수 
                max_main_chars = 900 if is_landscape else 1100
                max_extra_chars = 1600 if is_landscape else 1800 
                # ------------------------------------------------
                
                # 자체 텍스트 분할 알고리즘
                chunks = []
                current_chunk = ""
                is_first = True
                
                for line in script_text.split('\n'):
                    limit = max_main_chars if is_first else max_extra_chars
                    
                    if len(current_chunk) + len(line) + 1 > limit:
                        # 띄어쓰기(단어) 단위로 쪼개기
                        for word in line.split(' '):
                            if len(current_chunk) + len(word) + 1 > limit:
                                chunks.append(current_chunk.strip())
                                is_first = False
                                limit = max_extra_chars
                                current_chunk = word + " "
                            else:
                                current_chunk += word + " "
                        current_chunk += "\n"
                    else:
                        current_chunk += line + "\n"
                
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                
                # 2. 메인 페이지 우측 여백에 첫 번째 조각(Chunk) 삽입
                text_rect = fitz.Rect(base_w + 10, 15, FINAL_WIDTH - 10, FINAL_HEIGHT - 15)
                out_page.insert_textbox(
                    text_rect, 
                    chunks[0], 
                    fontsize=10.0, 
                    fontname="malgun", 
                    align=fitz.TEXT_ALIGN_LEFT
                )
                
                # 3. 텍스트가 남았다면 필요한 만큼 무한대로 뒷페이지 생성
                for i in range(1, len(chunks)):
                    extra_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
                    extra_page.insert_font(fontname="malgun", fontfile=font_path)
                    
                    # 새 페이지 좌측 상단에 원본 슬라이드 1/4 크기로 축소 삽입
                    mini_w = scaled_width * 0.5
                    mini_h = scaled_height * 0.5
                    mini_rect = fitz.Rect(20, 20, 20 + mini_w, 20 + mini_h)
                    extra_page.show_pdf_page(mini_rect, doc, pno)
                    
                    # 텍스트 영역: 미니 슬라이드 오른쪽 빈 공간부터 전체 활용
                    extra_text_rect = fitz.Rect(
                        20 + mini_w + 15,  
                        20,                
                        FINAL_WIDTH - 15,  
                        FINAL_HEIGHT - 20  
                    )
                    
                    extra_page.insert_textbox(
                        extra_text_rect, 
                        chunks[i], 
                        fontsize=10.0, 
                        fontname="malgun", 
                        align=fitz.TEXT_ALIGN_LEFT
                    )
        
        progress = (pno + 1) / total_pages
        progress_bar.progress(progress)
        status_text.caption(f"대본 매칭 및 동적 페이지 생성 중... 📝 ({pno + 1} / {total_pages} 슬라이드 완료)")
        
    out_bytes = out_doc.write()
    doc.close()
    out_doc.close()
    return out_bytes

# --- 🎨 Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기 (무한 확장형)")
st.markdown("PDF를 업로드하고 대본을 입력하면, 긴 대본은 자동으로 1/4 슬라이드와 함께 뒷페이지로 연장됩니다.")
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
