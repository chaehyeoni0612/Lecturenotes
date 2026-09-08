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

# --- 대본 텍스트 파싱 함수 (태그 없는 족보/추가 텍스트 누락 방지 보완) ---
def parse_script_text(text):
    # [숫자페이지] 기준으로 텍스트를 안전하게 쪼개는 로직
    splits = re.split(r'\[(\d+)페이지\]', text)
    script_dict = {}
    
    # splits[0]은 첫 태그 앞의 잔여 텍스트이므로 무시하고, (번호, 내용) 쌍으로 처리
    for i in range(1, len(splits), 2):
        if i + 1 < len(splits):
            page_num = int(splits[i])
            content = splits[i+1].strip()
            # 만약 같은 페이지 번호가 여러 개면 내용 합치기
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
        orig_rect = page.rect
        orig_width, orig_height = orig_rect.width, orig_rect.height
        
        FINAL_WIDTH = orig_width + MARGIN_PT
        FINAL_HEIGHT = orig_height
        
        out_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
        out_page.draw_rect(out_page.rect, color=(1, 1, 1), fill=(1, 1, 1))
        
        source_rect = fitz.Rect(0, 0, orig_width, orig_height)
        out_page.show_pdf_page(source_rect, doc, pno)
        
        current_page_num = pno + 1
        script_text = script_dict.get(current_page_num, "").strip()
        
        if script_text:
            out_page.insert_font(fontname="malgun", fontfile=font_path)
            
            is_landscape = orig_width > orig_height
            max_main_chars = 950 if is_landscape else 1150
            max_extra_chars = 1600 if is_landscape else 1800 
            
            max_main_lines = 38 if is_landscape else 48
            max_extra_lines = 45 if is_landscape else 55
            
            chunks = []
            current_chunk = ""
            current_chars = 0
            current_lines = 0
            
            for line in script_text.split('\n'):
                limit_chars = max_main_chars if len(chunks) == 0 else max_extra_chars
                limit_lines = max_main_lines if len(chunks) == 0 else max_extra_lines
                
                if current_lines >= limit_lines and current_chunk.strip():
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                    current_chars = 0
                    current_lines = 0
                    limit_chars = max_extra_chars
                
                if current_chars + len(line) > limit_chars:
                    for word in line.split(' '):
                        if current_chars + len(word) > limit_chars and current_chunk.strip():
                            chunks.append(current_chunk.strip())
                            current_chunk = ""
                            current_chars = 0
                            current_lines = 0
                            limit_chars = max_extra_chars
                            
                        current_chunk += word + " "
                        current_chars += len(word) + 1
                    
                    current_chunk += "\n"
                    current_lines += 1
                else:
                    current_chunk += line + "\n"
                    current_chars += len(line) + 1
                    current_lines += 1
            
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            
            if chunks:
                text_rect = fitz.Rect(orig_width + 10, 15, FINAL_WIDTH - 10, FINAL_HEIGHT - 15)
                out_page.insert_textbox(
                    text_rect, 
                    chunks[0], 
                    fontsize=10.0, 
                    fontname="malgun", 
                    align=fitz.TEXT_ALIGN_LEFT
                )
                
                for i in range(1, len(chunks)):
                    extra_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
                    extra_page.draw_rect(extra_page.rect, color=(1, 1, 1), fill=(1, 1, 1))
                    extra_page.insert_font(fontname="malgun", fontfile=font_path)
                    
                    mini_w = orig_width * 0.5
                    mini_h = orig_height * 0.5
                    mini_rect = fitz.Rect(20, 20, 20 + mini_w, 20 + mini_h)
                    extra_page.show_pdf_page(mini_rect, doc, pno)
                    
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
