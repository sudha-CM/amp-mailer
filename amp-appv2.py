import io
import hashlib
import re
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import requests
import streamlit as st
from streamlit.components.v1 import html as st_html
from PIL import Image

st.set_page_config(page_title="AMP Mailer v2", page_icon="📧", layout="wide")
st.title("AMP Mailer — v2 (images + quiz inputs)")

import os, requests, io
from PIL import Image

def _diag_try_direct_upload():
    cloud = st.secrets.get("CLOUDINARY_CLOUD_NAME", "")
    preset = st.secrets.get("CLOUDINARY_UPLOAD_PRESET", "")
    if not (cloud and preset):
        st.error("Secrets missing — cannot test direct upload.")
        return
    # make a tiny 1x1 PNG in memory
    img = Image.new("RGB", (1,1), color=(0,0,0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    url = f"https://api.cloudinary.com/v1_1/{cloud}/image/upload"
    try:
        import certifi
        r = requests.post(url, files={"file": buf.getvalue()},
                          data={"upload_preset": preset, "public_id": "diag-pixel"},
                          timeout=30, verify=certifi.where()
                          )                         
        st.write("Diag status:", r.status_code)
        try:
            st.json(r.json())
        except Exception:
            st.text(r.text)
    except Exception as e:
        st.exception(e)


AMP_PATH = Path("templates/AMP_Template.html")
FALLBACK_PATH = Path("templates/Fallback_Template.html")

# ---------- Load templates safely ----------
amp_ok = AMP_PATH.exists()
fb_ok  = FALLBACK_PATH.exists()
amp_src = AMP_PATH.read_text(encoding="utf-8") if amp_ok else ""
fb_src  = FALLBACK_PATH.read_text(encoding="utf-8") if fb_ok else ""

# ---------- Helpers ---------- this is the new version
def replace_tokens(html: str, mapping: dict) -> str:
    out = html
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out



def strip_optional_blocks(html: str, placeholder_urls: list[str]) -> str:
    """Remove whole containers that only exist to show placeholder images.

    This prevents awkward blank blocks when the user doesn't upload an image for that section.
    Works for both AMP (<amp-img>) and fallback (<img>) templates.
    """
    out = html
    for url in placeholder_urls:
        u = re.escape(url)

        # Remove common wrapper blocks that contain only the placeholder image (div/a wrappers)
        out = re.sub(
            rf"""<div\b[^>]*>\s*(?:<a\b[^>]*>\s*)?(?:<amp-img\b[^>]*\bsrc=['\"]{u}['\"][^>]*>\s*</amp-img>|<img\b[^>]*\bsrc=['\"]{u}['\"][^>]*>)\s*(?:</a>\s*)?</div>""",
            "<!-- removed optional image block -->",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Remove common table cell blocks that contain only the placeholder image
        out = re.sub(
            rf"""<td\b[^>]*>\s*(?:<a\b[^>]*>\s*)?(?:<amp-img\b[^>]*\bsrc=['\"]{u}['\"][^>]*>\s*</amp-img>|<img\b[^>]*\bsrc=['\"]{u}['\"][^>]*>)\s*(?:</a>\s*)?</td>""",
            "<!-- removed optional image cell -->",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # As a fallback, remove the image tags themselves
        out = re.sub(
            rf"""<amp-img\b[^>]*\bsrc=['\"]{u}['\"][^>]*>\s*</amp-img>""",
            "",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )
        out = re.sub(
            rf"""<img\b[^>]*\bsrc=['\"]{u}['\"][^>]*>""",
            "",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )

    # Clean up now-empty divs that can be left behind
    out = re.sub(r"<div\b[^>]*>\s*(?:<!--.*?-->\s*)*</div>", "", out, flags=re.IGNORECASE | re.DOTALL)
    return out
def amp_basics_ok(amp_html: str) -> list:
    errs = []
    if "⚡4email" not in amp_html and "amp4email" not in amp_html:
        errs.append("Missing ⚡4email on <html>.")
    if "https://cdn.ampproject.org/v0.js" not in amp_html:
        errs.append("Missing core AMP script.")
    if "<style amp4email-boilerplate" not in amp_html:
        errs.append("Missing amp4email boilerplate.")
    return errs

def cloudinary_upload(file_bytes: bytes, public_id: str) -> dict:
    cloud = st.secrets.get("CLOUDINARY_CLOUD_NAME", "")
    preset = st.secrets.get("CLOUDINARY_UPLOAD_PRESET", "")
    if not (cloud and preset):
        st.error(f"Secrets missing. CLOUDINARY_CLOUD_NAME={repr(cloud)}, CLOUDINARY_UPLOAD_PRESET={repr(preset)}")
        raise RuntimeError("Cloudinary not configured in secrets.toml")
    url = f"https://api.cloudinary.com/v1_1/{cloud}/image/upload"
    files = {"file": file_bytes}
    data = {
        "upload_preset": preset,
        "public_id": public_id,
        "overwrite": "true",
        "invalidate": "true",
    }
    import certifi
    r = requests.post(url, files=files, data=data, timeout=60, verify=certifi.where())
    if r.status_code >= 400:
        st.error(f"Cloudinary error {r.status_code}: {r.text}")
        r.raise_for_status()
    j = r.json()
    return {"url": j["secure_url"], "width": j.get("width"), "height": j.get("height")}


def dims(file_bytes: bytes):
    im = Image.open(io.BytesIO(file_bytes))
    return im.width, im.height

def send_v6(subject: str, to_email: str, amp_html: str, fallback_html: str, preheader: str = ""):
    """
    Send via Netcore v6 using 3-part content: text/plain + text/x-amp-html + text/html.
    Adjust header 'api_key' vs 'Authorization' to match your account.
    """
    text_part = f"{subject}\n\n{preheader}" if preheader else subject
    payload = {
        "from": {"email": st.secrets.get("FROM_EMAIL", ""), "name": st.secrets.get("FROM_NAME", "")},
        "subject": subject,
        "personalizations": [{"to": [{"email": to_email or st.secrets.get('DEFAULT_TEST_TO','') }]}],
        "content": [
            {"type": "text/plain",      "value": text_part},
            {"type": "text/x-amp-html", "value": amp_html},
            {"type": "text/html",       "value": fallback_html}
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "api_key": st.secrets.get("NETCORE_API_KEY", "")
    }
    url = st.secrets.get("NETCORE_SEND_URL", "")
    return requests.post(url, headers=headers, json=payload, timeout=60)



# Helper: try Cloudinary if configured, otherwise just detect dimensions
def _handle_upload(file, public_id, placeholder_url, default_w, default_h):
    url, w, h = placeholder_url, default_w, default_h
    if not file:
        return url, w, h

    # Streamlit UploadedFile can behave like a one-time stream on reruns.
    # getvalue() is safest and consistent.
    data = file.getvalue()

    # Create a unique public_id so Cloudinary returns a new URL when the file changes.
    content_hash = hashlib.sha1(data).hexdigest()[:10]
    unique_public_id = f"{public_id}-{content_hash}"

    try:
        up = cloudinary_upload(data, unique_public_id)
        url, w, h = up["url"], up.get("width", default_w), up.get("height", default_h)
        st.success(f"{public_id} uploaded: {w}×{h}")
    except Exception:
        # Cloudinary not set or blocked — fall back to local dim detection
        try:
            w, h = dims(data)
            st.warning(f"{public_id}: using placeholder URL but real width/height inserted ({w}×{h}).")
        except Exception:
            st.warning(f"{public_id}: using default size {default_w}×{default_h}.")
    return url, w, h

# ---------- Inputs (Images + CTA + Quiz labels only) ----------
# ---------- Inputs + CTA + Quiz labels ----------

st.markdown("## Inputs")

with st.form("amp_inputs"):
    cta_url = st.text_input("Primary CTA URL", value="https://example.com", key="cta_url")
    quiz_product_url = st.text_input("Quiz product URL", value="https://example.com/collection", key="quiz_product_url")

    st.markdown("### Images")
    colA, colB = st.columns(2)
    with colA:
        logo_up = st.file_uploader("Logo (png/jpg/jpeg/svg)", type=["png", "jpg", "jpeg", "svg"], key="logo_up")
    with colB:
        hero_up = st.file_uploader("Hero image 1 (png/jpg/jpeg)", type=["png", "jpg", "jpeg"], key="hero_up")

    colC, colD = st.columns(2)
    with colC:
        hero2_up = st.file_uploader("Hero image 2 (optional)", type=["png", "jpg", "jpeg"], key="hero2_up")
    with colD:
        quiz_img_up = st.file_uploader("Quiz header image (optional)", type=["png", "jpg", "jpeg"], key="quiz_img_up")

    colE, colF = st.columns(2)
    with colE:
        quiz_product_up = st.file_uploader("Quiz product image", type=["png", "jpg", "jpeg"], key="quiz_product_up")
    with colF:
        footer1_up = st.file_uploader("Footer image 1 (optional)", type=["png", "jpg", "jpeg"], key="footer1_up")

    footer2_up = st.file_uploader("Footer image 2 (optional)", type=["png", "jpg", "jpeg"], key="footer2_up")

    st.markdown("### Quiz — Question 1")
    quiz_question   = st.text_input("Q1: Question", value="What would you like to shop?", key="quiz_question")
    quiz_opt1_label = st.text_input("Q1: Option 1 label", value="New Arrivals", key="quiz_opt1_label")
    quiz_opt2_label = st.text_input("Q1: Option 2 label", value="Best Sellers", key="quiz_opt2_label")
    quiz_opt3_label = st.text_input("Q1: Option 3 label", value="Sale", key="quiz_opt3_label")
    quiz_opt4_label = st.text_input("Q1: Option 4 label", value="Gifts", key="quiz_opt4_label")

    st.markdown("### Quiz — Question 2")
    quiz2_question   = st.text_input("Q2: Question", value="Which category are you browsing today?", key="quiz2_question")
    quiz2_opt1_label = st.text_input("Q2: Option 1 label", value="Dresses", key="quiz2_opt1_label")
    quiz2_opt2_label = st.text_input("Q2: Option 2 label", value="Shoes", key="quiz2_opt2_label")
    quiz2_opt3_label = st.text_input("Q2: Option 3 label", value="Tops and Tees", key="quiz2_opt3_label")
    quiz2_opt4_label = st.text_input("Q2: Option 4 label", value="Skirts and Pants", key="quiz2_opt4_label")

    st.caption("Per your rule: we only change visible labels/text in the AMP template, never the underlying option values.")

    submitted = st.form_submit_button("Generate AMP email")

if not submitted:
    st.info("Fill the inputs above and click **Generate AMP email**. Nothing will upload or change until you submit.")
    st.stop()

# From here onward, we apply ALL changes in one go (uploads + token replacement).

# Default placeholders so template still renders even without uploads
logo_url, logo_w, logo_h = "https://via.placeholder.com/160x48?text=Logo", 160, 48
hero_url, hero_w, hero_h = "https://via.placeholder.com/1200x600?text=Hero+1", 1200, 600
hero2_url, hero2_w, hero2_h = "https://via.placeholder.com/1200x600?text=Hero+2", 1200, 600
quiz_img_url, quiz_w, quiz_h = "https://via.placeholder.com/600x300?text=Quiz+Image", 600, 300
quiz_product_img_url, quiz_product_w, quiz_product_h = "https://via.placeholder.com/600x600?text=Quiz+Product", 600, 600
footer1_img_url, footer1_w, footer1_h = "https://via.placeholder.com/1088x552?text=Footer+1", 1088, 552
footer2_img_url, footer2_w, footer2_h = "https://via.placeholder.com/1086x954?text=Footer+2", 1086, 954

# Apply uploads (if provided) - happens only after Submit
logo_url, logo_w, logo_h = _handle_upload(logo_up, "logo", logo_url, logo_w, logo_h)
hero_url, hero_w, hero_h = _handle_upload(hero_up, "hero1", hero_url, hero_w, hero_h)
hero2_url, hero2_w, hero2_h = _handle_upload(hero2_up, "hero2", hero2_url, hero2_w, hero2_h)
quiz_img_url, quiz_w, quiz_h = _handle_upload(quiz_img_up, "quiz-header", quiz_img_url, quiz_w, quiz_h)
quiz_product_img_url, quiz_product_w, quiz_product_h = _handle_upload(quiz_product_up, "quiz-product", quiz_product_img_url, quiz_product_w, quiz_product_h)
footer1_img_url, footer1_w, footer1_h = _handle_upload(footer1_up, "footer1", footer1_img_url, footer1_w, footer1_h)
footer2_img_url, footer2_w, footer2_h = _handle_upload(footer2_up, "footer2", footer2_img_url, footer2_w, footer2_h)

# ---------- Build token map & generate AMP ----------
token_map = {
    # images
    "logo_img_url":         logo_url,
    "logo_width":           logo_w,
    "logo_height":          logo_h,
    "hero_img_url":         hero_url,
    "hero_width":           hero_w,
    "hero_height":          hero_h,
    "hero2_img_url":        hero2_url,
    "hero2_width":          hero2_w,
    "hero2_height":         hero2_h,
    "quiz_img_url":         quiz_img_url,
    "quiz_width":           quiz_w,
    "quiz_height":          quiz_h,
    "quiz_product_img_url": quiz_product_img_url,
    "quiz_product_width":   quiz_product_w,
    "quiz_product_height":  quiz_product_h,
    "footer1_img_url":      footer1_img_url,
    "footer1_width":        footer1_w,
    "footer1_height":       footer1_h,
    "footer2_img_url":      footer2_img_url,
    "footer2_width":        footer2_w,
    "footer2_height":       footer2_h,

    # links
    "cta_url":              cta_url,
    "quiz_product_url":     quiz_product_url,

    # quiz labels
    "quiz_question":        quiz_question,
    "quiz_opt1_label":      quiz_opt1_label,
    "quiz_opt2_label":      quiz_opt2_label,
    "quiz_opt3_label":      quiz_opt3_label,
    "quiz_opt4_label":      quiz_opt4_label,

    "quiz2_question":       quiz2_question,
    "quiz2_opt1_label":     quiz2_opt1_label,
    "quiz2_opt2_label":     quiz2_opt2_label,
    "quiz2_opt3_label":     quiz2_opt3_label,
    "quiz2_opt4_label":     quiz2_opt4_label,
}


amp_final = replace_tokens(amp_src, token_map)

# Remove blocks that would otherwise render placeholder-image spacing
placeholder_urls = [logo_url, hero_url, hero2_url, quiz_img_url, quiz_product_img_url, footer1_img_url, footer2_img_url]
amp_final = strip_optional_blocks(amp_final, placeholder_urls)
errs = amp_basics_ok(amp_final)
if errs:
    st.error("AMP checks: " + "; ".join(errs))

# ---------- AMP-only preview ----------
st.markdown("## Preview (AMP only)")
tab_code, tab_preview = st.tabs(["AMP Code", "AMP Preview"])
with tab_code:
    st.subheader("AMP — full source (after replacements)")
    st.code(amp_final, language="html")
with tab_preview:
    st.subheader("AMP — visual preview")
    st.info("Preview is approximate in Streamlit. Use HTTPS image URLs + explicit width/height for <amp-img>.")
    st_html(amp_final, height=900, scrolling=True)

st.download_button("Download AMP HTML", amp_final, file_name="amp.html", mime="text/html")

# ---------- Send Test (uses fallback behind the scenes) ----------
st.markdown("---")
st.subheader("Send test")

subject   = st.text_input("Subject", "")
preheader = st.text_input("Preheader (optional)", "")
to_email  = st.text_input("To (blank uses DEFAULT_TEST_TO)", "")

has_secrets = all([
    bool(st.secrets.get("NETCORE_API_KEY", "")),
    bool(st.secrets.get("NETCORE_SEND_URL", "")),
    bool(st.secrets.get("FROM_EMAIL", "")),
    bool(st.secrets.get("FROM_NAME", "")),
])
has_templates = bool(amp_ok and fb_ok)

if not has_templates:
    st.warning("Templates missing. Ensure both files exist in /templates.")
if not has_secrets:
    st.info("Add NETCORE_API_KEY, NETCORE_SEND_URL, FROM_EMAIL, FROM_NAME (and DEFAULT_TEST_TO) to .streamlit/secrets.toml to enable sending.")

send_btn = st.button("Send Test Email", disabled=not (has_templates and has_secrets))
if send_btn:
    resp = send_v6(subject, to_email, amp_final, fb_src, preheader)
    st.write("Status:", resp.status_code)
    try:
        st.json(resp.json())
    except Exception:
        st.text(resp.text)
