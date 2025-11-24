import io
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

st.write("Working dir:", os.getcwd())
st.write("Cloudinary cloud:", repr(st.secrets.get("CLOUDINARY_CLOUD_NAME", "")))
st.write("Cloudinary preset:", repr(st.secrets.get("CLOUDINARY_UPLOAD_PRESET", "")))

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

with st.expander("Cloudinary diagnostics", expanded=True):
    if st.button("Run Cloudinary test upload"):
        _diag_try_direct_upload()


AMP_PATH = Path("templates/AMP_Template.html")
FALLBACK_PATH = Path("templates/Fallback_Template.html")

# ---------- Load templates safely ----------
amp_ok = AMP_PATH.exists()
fb_ok  = FALLBACK_PATH.exists()
amp_src = AMP_PATH.read_text(encoding="utf-8") if amp_ok else ""
fb_src  = FALLBACK_PATH.read_text(encoding="utf-8") if fb_ok else ""

# ---------- Helpers ----------
def replace_tokens(html: str, mapping: dict) -> str:
    out = html
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", str(v))
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
    data  = {"upload_preset": preset, "public_id": public_id}
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


# ---------- Inputs (Images + CTA + Quiz labels only) ----------
st.markdown("## Inputs")

cta_url = st.text_input("Primary CTA URL", value="https://example.com")

colA, colB = st.columns(2)
with colA:
    logo_up = st.file_uploader("Logo (png/jpg/jpeg/svg)", type=["png","jpg","jpeg","svg"])
with colB:
    hero_up = st.file_uploader("Hero (png/jpg/jpeg)", type=["png","jpg","jpeg"])

# Default placeholders (work even if Cloudinary isn’t configured yet)
logo_url = "https://via.placeholder.com/160x48?text=Logo"
hero_url = "https://via.placeholder.com/1200x600?text=Hero"
logo_w, logo_h = 160, 48
hero_w, hero_h = 1200, 600

# Try Cloudinary hosting if secrets present; otherwise keep placeholders
if logo_up:
    data = logo_up.read()
    try:
        up = cloudinary_upload(data, f"logo-{logo_up.name}")
        logo_url, logo_w, logo_h = up["url"], up["width"], up["height"]
        st.success(f"Logo uploaded: {logo_w}×{logo_h}")
    except Exception:
        # no Cloudinary configured; still detect real size for tokens
        if logo_up.type != "image/svg+xml":
            logo_w, logo_h = dims(data)
            st.warning("Cloudinary not set — using placeholder URL but real width/height inserted.")
        else:
            st.warning("SVG uploaded; keeping default 160×48 unless you host it and set exact size.")

if hero_up:
    data = hero_up.read()
    try:
        up = cloudinary_upload(data, f"hero-{hero_up.name}")
        hero_url, hero_w, hero_h = up["url"], up["width"], up["height"]
        st.success(f"Hero uploaded: {hero_w}×{hero_h}")
    except Exception:
        hero_w, hero_h = dims(data)
        st.warning("Cloudinary not set — using placeholder URL but real width/height inserted.")

st.markdown("### Quiz (labels only — values remain unchanged in the AMP)")
quiz_question   = st.text_input("Quiz Question", value="Which style do you like most?")
quiz_opt1_label = st.text_input("Option 1 label", value="Classic")
quiz_opt2_label = st.text_input("Option 2 label", value="Modern")
quiz_opt3_label = st.text_input("Option 3 label", value="Minimal")
quiz_opt4_label = st.text_input("Option 4 label", value="Bold")

st.caption("Per your rule: we do NOT edit the underlying value attributes in the AMP; only visible labels are replaced.")

# ---------- Build token map & generate AMP ----------
token_map = {
    # images
    "logo_img_url":  logo_url,
    "logo_width":    logo_w,
    "logo_height":   logo_h,
    "hero_img_url":  hero_url,
    "hero_width":    hero_w,
    "hero_height":   hero_h,
    # links
    "cta_url":       cta_url,
    # quiz labels only (values untouched in template)
    "quiz_question":    quiz_question,
    "quiz_opt1_label":  quiz_opt1_label,
    "quiz_opt2_label":  quiz_opt2_label,
    "quiz_opt3_label":  quiz_opt3_label,
    "quiz_opt4_label":  quiz_opt4_label,
}

amp_final = replace_tokens(amp_src, token_map)
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
