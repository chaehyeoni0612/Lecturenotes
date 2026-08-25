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

# --- 메인 PDF 처리 함수 (동적 페이지 추가 및 1/4 슬라이드 배치) ---
def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text):
    CM_TO_PT = 28.3465
    MARGIN_PT = 8.0 * CM_TO_PT  # 기본 오른쪽 여백 8cm
    
    font_path = get_korean_font()
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
    total_pages = len(doc)
    
    for pno in range(total_pages):
        page = doc[pno]
        rect = page.rect
        orig_width, orig_height = rect.width, rect.height
        
        # 1. 원본 방향 감지하여 A4 크기 유동적 설정
        if orig_width > orig_height:
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
                
                # 메인 페이지 우측 여백 박스
                text_rect = fitz.Rect(
                    base_w + 10,       # x0 
                    15,                # y0 
                    FINAL_WIDTH - 10,  # x1 
                    FINAL_HEIGHT - 15  # y1 
                )
                
                # PyMuPDF 스토리(Story) 기능을 활용해 텍스트가 넘치는 지점 감지 및 동적 페이지 추가
                story = fitz.Story(script_text, fontname="malgun", fontfile=font_path, fontsize=9.0)
                
                # 첫 번째 메인 페이지에 텍스트 넣기 시도
                # story.place()는 박스 안에 텍스트를 흘려보내고, 남은 텍스트가 있는지 여부를 반환합니다.
                try:
                    recs = story.place(text_rect)
                    story.draw(out_page)
                except Exception:
                    recs = None

                # --- [2단계] 만약 텍스트가 넘치면 뒷페이지들을 동적으로 계속 생성 ---
                # 스토리 내에 남은 텍스트가 존재한다면 루프를 돌며 페이지 추가
                while story.has_more:
                    # 새로운 빈 페이지 추가 (메인과 동일한 규격)
                    extra_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
                    extra_page.insert_font(fontname="malgun", fontfile=font_path)
                    
                    # 새 페이지 좌측 상단에 원본 슬라이드를 1/4 사이즈로 축소하여 배치
                    # 1/4 크기(스케일 0.5)로 줄여서 (15, 15) 위치에 꽂아줌
                    mini_w = scaled_width * 0.5
                    mini_h = scaled_height * 0.5
                    mini_rect = fitz.Rect(15, 15, 15 + mini_w, 15 + mini_h)
                    extra_page.show_pdf_page(mini_rect, doc, pno)
                    
                    # 새 페이지에서 텍스트가 들어갈 영역 정의
                    # 왼쪽에는 1/4 슬라이드가 있으므로, 우측 넓은 공간 및 아래쪽 전체를 대본 영역으로 활용
                    extra_text_rect = fitz.Rect(
                        15 + mini_w + 15,  # x0 (미니 슬라이드 우측 + 여백)
                        15,                # y0 
                        FINAL_WIDTH - 10,  # x1 
                        FINAL_HEIGHT - 15  # y1 
                    )
                    
                    # 만약 대본 양이 너무 많아 우측 공간도 부족하면 아래쪽 전체 공간 활용을 위한 확장 영역 지정 가능
                    # 여기서는 안전하게 extra_text_rect에 계속 이어서 드로잉
                    try:
                        story.place(extra_text_rect)
                        story.draw(extra_page)
                    except Exception:
                        break
        
        progress = (pno + 1) / total_pages
        progress_bar.progress(progress)
        status_text.caption(f"대본 매칭 및 동적 페이지 생성 중... 📝 ({pno + 1} / {total_pages} 슬라이드 완료)")
        
    out_bytes = out_doc.write()
    doc.close()
    out_doc.close()
    return out_bytes

# --- 🎨 Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기 (동적 페이지 확장형)")
st.markdown("PDF를 업로드하고 대본을 입력하면, 긴 대본은 자동으로 뒷페이지에 1/4 슬라이드와 함께 연장되어 생성됩니다.")
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
    if st.button("✨ PDF 변환 및 동적 매칭 실행", type="primary", use_container_width=True):
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
