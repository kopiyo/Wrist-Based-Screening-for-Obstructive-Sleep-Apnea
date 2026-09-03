"""
Sleep apnea screening from a single ECG lead.

    pip install -r requirements.txt
    python app.py

Needs model/cnn_bilstm.pt, model/stage2.pkl, model/config.json — download them
from /kaggle/working/model/ after running the training notebook.
"""
import json, os, traceback
from pathlib import Path

import numpy as np
import gradio as gr
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure

import pipeline as P

HERE = Path(__file__).parent
SAMPLE_DIR = HERE / "samples"

LOAD_ERROR = None
try:
    MODEL, STAGE2, CFG = P.load_artifacts()
except Exception as e:
    MODEL = STAGE2 = CFG = None
    LOAD_ERROR = str(e)

SAMPLES = {}
if (SAMPLE_DIR / "index.json").exists():
    SAMPLES = json.load(open(SAMPLE_DIR / "index.json"))
DISPLAY_SAMPLES = {key.replace(" — ", "; "): key for key in SAMPLES}

# --- Palette ---------------------------------------------------------------
INK      = "#0F172A"
BODY     = "#334155"
MUTED    = "#64748B"
HAIRLINE = "#E2E8F0"
CANVAS   = "#F1F5F9"
RED      = "#B91C1C"
RED_BG   = "#FEF2F2"
GREEN    = "#15803D"
GREEN_BG = "#F0FDF4"
ACCENT   = "#0891B2"

FONT = "'DM Sans', sans-serif"

CSS = """
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

.gradio-container {
    max-width: 1160px !important;
  margin: 0 auto !important;
        overflow-x: hidden !important;
    font-family: 'DM Sans', sans-serif !important;
    padding: 0 28px 56px !important;
    background: linear-gradient(180deg, rgba(255,255,255,.72), rgba(241,245,249,.92)),
        repeating-linear-gradient(90deg, transparent 0, transparent 79px,
            rgba(15,23,42,.025) 80px);
}
.gradio-container * {
    font-family: 'DM Sans', sans-serif !important;
}
.gradio-container h1, .gradio-container h2, .gradio-container h3 {
    font-family: 'Space Grotesk', 'DM Sans', sans-serif !important;
}
.hero-shell {
    position: relative;
    width: auto;
    margin: 0;
    padding: 30px 28px 24px !important;
    box-sizing: border-box;
    background: #0B1220;
    box-shadow: 0 0 0 100vmax #0B1220;
    clip-path: inset(0 -100vmax);
    animation: rise-in .65s ease both;
}
.card {
    animation: rise-in .7s .08s ease both;
    transition: box-shadow .25s ease, border-color .25s ease;
}
.card:hover {
    border-color: #CBD5E1 !important;
    box-shadow: 0 14px 32px rgba(15,23,42,.08) !important;
}
.result-shell {
    margin-top: 40px !important;
    animation: result-in .6s ease both;
}
.result-chart {
    border-radius: 12px !important;
    overflow: hidden !important;
    margin-top: 24px !important;
}
.result-disclaimer {
    margin-top: 56px !important;
}
.stat-tile { transition: transform .22s ease, background .22s ease; }
.stat-tile:hover { transform: translateY(-3px); background: #FFFFFF !important; }
@keyframes rise-in {
    from { opacity: 0; transform: translateY(14px); }
    to { opacity: 1; transform: translateY(0); }
}
@keyframes result-in {
    from { opacity: 0; transform: scale(.985) translateY(10px); }
    to { opacity: 1; transform: scale(1) translateY(0); }
}
@media (max-width: 700px) {
    .gradio-container { padding: 0 14px 36px !important; }
    .card { padding: 20px !important; }
    .hero-shell { padding-left: 22px !important; padding-right: 22px !important; }
}

.card {
  background: #FFFFFF !important;
  border: 1px solid #E2E8F0 !important;
    border-radius: 12px !important;
    padding: 24px 26px !important;
    box-shadow: 0 4px 18px rgba(15,23,42,.045) !important;
}
.bare {
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
}

.eyebrow {
  font-size: 11.5px; font-weight: 600; letter-spacing: .09em;
  text-transform: uppercase; color: #64748B; margin-bottom: 14px;
}

.gradio-container label span {
  font-size: 13.5px !important; font-weight: 500 !important; color: #334155 !important;
}
.gradio-container input, .gradio-container select {
  font-size: 15px !important; padding: 11px 13px !important;
    border-radius: 8px !important;
}
.gradio-container .gr-dropdown,
.gradio-container .wrap[data-testid="dropdown"] {
    min-height: 54px !important;
}
.gradio-container .gr-dropdown input {
    cursor: pointer !important;
}
button.primary, .gr-button-primary {
    border-radius: 8px !important; font-size: 15px !important;
  font-weight: 600 !important; padding: 13px 22px !important;
    background: linear-gradient(135deg, #0891B2, #0E7490) !important;
    box-shadow: 0 8px 16px rgba(8,145,178,.25) !important;
    transition: transform .2s ease, box-shadow .2s ease !important;
}
button.primary:hover, .gr-button-primary:hover {
    transform: translateY(-1px);
    box-shadow: 0 11px 22px rgba(8,145,178,.25) !important;
}
.gradio-container .tab-nav button {
  font-size: 14.5px !important; font-weight: 500 !important;
    padding: 8px 16px !important;
}
.gradio-container .gap { gap: 18px !important; }
.patient-details .row {
    gap: 12px !important;
    border: none !important;
    box-shadow: none !important;
}
.patient-details label,
.patient-details label span {
    text-align: left !important;
}
.patient-details .styler,
.patient-details .styler > .block,
.bare .styler {
    background: transparent !important;
}
.patient-details label span {
    background: transparent !important;
    padding-left: 0 !important;
    padding-right: 0 !important;
}
.bmi-caption {
    color: #64748B;
    font-size: 12px;
    line-height: 1.35;
    margin-top: 4px;
}
.footer-note {
    margin-top: 56px !important;
}
.compact-upload .upload-container,
.compact-upload .upload-dropbox,
.compact-upload .file-preview {
    min-height: 0 !important;
    height: auto !important;
}
.compact-upload .upload-dropbox { padding: 12px !important; }
.compact-upload .upload-text { margin: 4px 0 !important; }
.compact-csv .upload-dropbox { padding: 9px 12px !important; }
footer { display: none !important; }
"""

THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.cyan,
    neutral_hue=gr.themes.colors.slate,
    radius_size=gr.themes.sizes.radius_lg,
    spacing_size=gr.themes.sizes.spacing_lg,
).set(
    body_background_fill=CANVAS,
    block_background_fill="#FFFFFF",
    block_border_width="1px",
    block_shadow="none",
    button_primary_background_fill=ACCENT,
    button_primary_background_fill_hover="#0E7490",
)


# ---------------------------------------------------------------------------
# HTML fragments
# ---------------------------------------------------------------------------
def hero():
    return f"""
<div class="hero-shell" style="padding:28px 8px 18px">
  <div style="font-size:11.5px;font-weight:600;letter-spacing:.1em;
         text-transform:uppercase;color:{ACCENT};margin-bottom:12px">
    Deep learning &middot; Clinical decision support</div>
    <h1 style="font-size:32px;font-weight:700;color:#F8FAFC;margin:0 0 10px 0;
      letter-spacing:0;line-height:1.12;max-width:820px">
    AI powered sleep apnea screening from a single ECG lead</h1>
    <p style="font-size:14.5px;color:#CBD5E1;margin:0;line-height:1.5;
         max-width:760px;text-align:justify;text-justify:inter-word">
    Most obstructive sleep apnea goes undiagnosed because polysomnography is
    costly and hard to access. This model reads one overnight ECG channel, the
    kind a Holter monitor, chest strap or smartwatch already records, and
    estimates whether a sleep study is warranted.</p>
</div>"""


def stat(value, label, unit="", dominant=False):
                size = "40px" if dominant else "26px"
                span = "grid-column:span 2;" if dominant else ""
                return f"""<div class="stat-tile" style="{span}background:#F8FAFC;
                        border:1px solid {HAIRLINE};border-radius:10px;padding:13px 16px">
            <div style="font-size:11.5px;color:{MUTED};font-weight:600;
                     letter-spacing:.06em;text-transform:uppercase;margin-bottom:9px">
                {label}</div>
                        <div style="font-size:{size};font-weight:700;color:{INK};line-height:1;
                     letter-spacing:-.02em">{value}<span style="font-size:14px;
                     font-weight:500;color:{MUTED};letter-spacing:0"> {unit}</span></div>
        </div>"""


def severity_ribbon(apnea_index):
        bands = [("Normal", 5, "#D1FAE5"), ("Mild", 15, "#A7F3D0"),
                         ("Moderate", 30, "#FDE68A"), ("Severe", 60, "#FCA5A5")]
        marker = min(max(apnea_index, 0), 60) / 60 * 100
        return f"""
        <div style="margin-top:20px">
            <div style="display:flex;height:10px;border-radius:5px;overflow:hidden">
                <div style="width:8.33%;background:{bands[0][2]}"></div>
                <div style="width:16.67%;background:{bands[1][2]}"></div>
                <div style="width:25%;background:{bands[2][2]}"></div>
                <div style="width:50%;background:{bands[3][2]}"></div>
            </div>
            <div style="position:relative;height:25px;font-size:10.5px;color:{MUTED};margin-top:5px">
                <span style="position:absolute;left:0">Normal</span>
                <span style="position:absolute;left:8.33%;transform:translateX(-50%)">Mild</span>
                <span style="position:absolute;left:25%;transform:translateX(-50%)">Moderate</span>
                <span style="position:absolute;right:0">Severe</span>
                <span style="position:absolute;left:{marker:.1f}%;top:-20px;transform:translateX(-50%);
                         color:{INK};font-weight:700">{apnea_index:.1f}</span>
                <span style="position:absolute;left:{marker:.1f}%;top:-7px;width:2px;height:18px;
                         background:{INK};transform:translateX(-50%)"></span>
            </div>
        </div>"""


def result_card(res, label, truth=None):
    refer = res["refer"]
    accent, tint = (RED, RED_BG) if refer else (GREEN, GREEN_BG)
    verdict = "Refer for a sleep study" if refer else "No referral indicated"
    sub = ("Findings consistent with clinically significant sleep apnea"
           if refer else "Apnea burden below the referral threshold")

    truth_html = ""
    if truth is not None:
        pos = truth["AHI"] >= CFG["ahi_threshold"]
        agree = pos == refer
        truth_html = f"""
        <div style="margin-top:18px;background:#F8FAFC;border:1px solid {HAIRLINE};
             border-radius:13px;padding:16px 19px">
          <div style="font-size:11.5px;font-weight:600;letter-spacing:.06em;
               text-transform:uppercase;color:{MUTED};margin-bottom:8px">
            Reference standard</div>
          <div style="font-size:14.5px;color:{BODY};line-height:1.6">
            Clinician-measured AHI <b style="color:{INK}">{truth['AHI']:.1f}</b>
            {'Positive' if pos else 'Negative'} at AHI &ge; {CFG['ahi_threshold']:.0f}.
            <span style="color:{GREEN if agree else RED};font-weight:600">
            {'Model agrees.' if agree else 'Model disagrees.'}</span></div>
        </div>"""

        threshold = CFG["screening_threshold"]
        return f"""
<div class="result-shell" style="font-family:{FONT};margin-top:14px">
  <div style="background:{tint};border:1px solid {accent}25;border-radius:16px;
        padding:18px 24px;margin-bottom:0">
    <div style="font-size:11.5px;font-weight:700;letter-spacing:.1em;
         text-transform:uppercase;color:{accent};opacity:.8">Result</div>
    <div style="font-size:25px;font-weight:700;color:{accent};margin-top:6px;
         letter-spacing:-.015em">{verdict}</div>
    <div style="font-size:15px;color:{accent};opacity:.9;margin-top:7px;
         line-height:1.5">{sub}</div>
  </div>

    <div style="margin-top:16px;font-size:12.5px;color:{MUTED};line-height:1.5">
        {label} &nbsp;|&nbsp; {res['hours']:.1f} h recording &nbsp;|&nbsp;
        {res['n_beats']:,} beats detected &nbsp;|&nbsp;
        mean heart rate {res['heart_rate']:.0f} bpm
    </div>

    {severity_ribbon(res['apnea_index'])}

        <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;
                 margin-top:8px">
        {stat(f"{res['apnea_index']:.1f}", "Apnea burden", "min/hr", dominant=True)}
    {stat(f"{res['apnea_minutes']:,}", "Apneic minutes")}
    {stat(f"{res['minutes']:,}", "Minutes scored")}
        {stat(f"{res['risk']:.2f}<span style='font-size:12px;font-weight:500;color:{MUTED};letter-spacing:0'> / {threshold:.2f}</span>", "Risk score", "threshold")}
  </div>
  {truth_html}
</div>"""


def disclaimer():
    c = CFG
    return f"""
<div class="result-disclaimer" style="font-family:{FONT};background:#FFFFFF;border:1px solid {HAIRLINE};
    border-radius:11px;padding:15px 20px;margin-top:12px">
  <div style="font-size:11.5px;font-weight:600;letter-spacing:.08em;
       text-transform:uppercase;color:{MUTED};margin-bottom:10px">
    About this result</div>
    <div style="font-size:12.5px;color:{BODY};line-height:1.6">
    <b style="color:{INK}">Screening tool, not a diagnosis.</b>
    At this operating point the model identified
    <b style="color:{INK}">{100*c['screening_sensitivity']:.0f}%</b> of patients
    with AHI &ge; {c['ahi_threshold']:.0f} and correctly cleared
    <b style="color:{INK}">{100*c['screening_specificity']:.0f}%</b> of those
    below it, across {c['n_patients']} patients and {c['n_minutes']:,}
    expert-annotated minutes from the PhysioNet Apnea-ECG database.
    Cross-validated; not validated on an external cohort. The cohort was
    referred for suspected sleep-disordered breathing, so performance in an
    unselected population would differ. Research use only.
  </div>
</div>"""


def error_card(msg):
    return f"""<div style="font-family:{FONT};background:{RED_BG};
      border:1px solid #FCA5A5;border-radius:14px;padding:20px 24px;margin-top:26px">
      <div style="font-size:16px;font-weight:600;color:{RED}">
        Could not score this recording</div>
      <div style="margin-top:7px;font-size:14px;color:{RED};opacity:.9;
           line-height:1.6">{msg}</div></div>"""


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def night_figure(res):
    hrs = np.arange(res["minutes"]) / 60.0
    accent = RED if res["refer"] else GREEN
    fig = Figure(figsize=(11.2, 3.2), dpi=112)
    fig.patch.set_facecolor("white")
    ax1 = fig.add_subplot(211)
    ax2 = fig.add_subplot(212, sharex=ax1)

    ax1.set_facecolor("#F5FBFC")
    ax1.fill_between(hrs, 0, res["per_minute_pred"], step="post",
                     color=accent, alpha=.88, linewidth=0)
    ax1.set_ylim(0, 1); ax1.set_yticks([]); ax1.set_ylabel("")
    ax1.set_title("Apneic minutes shown in red", fontsize=9.5, loc="left",
                  color=MUTED, pad=5, fontweight="500")
    ax1.tick_params(labelbottom=False)

    ax2.plot(hrs, res["per_minute_prob"], lw=1.1, color=ACCENT)
    ax2.axhline(.5, color="#94A3B8", ls="--", lw=1)
    ax2.set_ylim(0, 1)
    ax2.axhspan(.5, 1, color=RED, alpha=.06, zorder=0)
    ax2.set_ylabel("P(apnea)", fontsize=10, color=MUTED)
    ax2.set_xlabel("Hours from start of recording", fontsize=10.5, color=MUTED)

    for ax in (ax1, ax2):
        if ax is ax2:
            ax.set_facecolor("white")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(HAIRLINE)
        ax.tick_params(colors=MUTED, labelsize=10)
        ax.grid(axis="x", color="#F1F5F9", lw=1)
    ax1.spines["left"].set_visible(False)
    fig.tight_layout(pad=1.4)
    return fig


def plot_placeholder():
    return """
    <div style="display:flex;flex-direction:column;align-items:center;
         justify-content:center;min-height:250px;background:#ECFEFF;
            border:1px solid #A5F3FC;border-radius:12px;color:#64748B;
            text-align:center">
      <svg width="48" height="32" viewBox="0 0 48 32" fill="none"
           aria-hidden="true" xmlns="http://www.w3.org/2000/svg">
        <path d="M1 17h9l3-8 5 17 5-22 5 13h8l3-5 3 5h5"
              stroke="#94A3B8" stroke-width="2" stroke-linecap="round"
              stroke-linejoin="round"/>
      </svg>
    <div style="margin-top:10px;font-size:14px">Select a patient and press Analyse</div>
    <div style="margin-top:4px;font-size:13px;color:#94A3B8">Results will appear here</div>
    </div>"""


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _run(ecg, fs, label, age, sex, height, weight, truth=None):
    res = P.screen(ecg, fs, MODEL, STAGE2, CFG, age=age, sex=sex,
                   height_cm=height, weight_kg=weight)
    return (result_card(res, label, truth),
            gr.update(value=night_figure(res), visible=True), disclaimer(),
            gr.update(visible=False))


def run_sample(name, age, sex, height, weight):
    if MODEL is None:
        return (error_card(LOAD_ERROR), gr.update(value=None, visible=False),
            "", gr.update(visible=True))
    try:
        record_key = DISPLAY_SAMPLES.get(name, name)
        meta = SAMPLES[record_key]
        sig, hdr = P.read_signal(str(SAMPLE_DIR / meta["record"]))
        truth = {"AHI": meta["AHI"]} if meta.get("AHI") is not None else None
        return _run(sig[:, 0], hdr["fs"], f"Sample patient {meta['record']}",
                    age, sex, height, weight, truth)
    except Exception as e:
        traceback.print_exc()
        return (error_card(str(e)), gr.update(value=None, visible=False),
            "", gr.update(visible=True))


def run_upload(hea_file, dat_file, csv_file, age, sex, height, weight):
    if MODEL is None:
        return (error_card(LOAD_ERROR), gr.update(value=None, visible=False),
            "", gr.update(visible=True))
    files = []
    for file in (hea_file, dat_file, csv_file):
        if file:
            files.append(getattr(file, "path", None) or str(file))
    if not files:
        return (error_card("Upload a WFDB pair (.hea and .dat) or a CSV first."),
            gr.update(value=None, visible=False), "", gr.update(visible=True))
    suffixes = {Path(file).suffix.lower() for file in files}
    if suffixes == {".dat"}:
        bundled_header = SAMPLE_DIR / (Path(files[0]).stem + ".hea")
        if bundled_header.exists():
            files.append(str(bundled_header))
    elif suffixes == {".hea"}:
        bundled_signal = SAMPLE_DIR / (Path(files[0]).stem + ".dat")
        if bundled_signal.exists():
            files.append(str(bundled_signal))
    try:
        ecg, fs, label = P.load_ecg_from_files(files)
        return _run(ecg, fs or CFG["fs"], f"Uploaded: {label}",
                    age, sex, height, weight)
    except Exception as e:
        traceback.print_exc()
        message = str(e)
        if suffixes & {".hea", ".dat"} and not {".hea", ".dat"}.issubset(
                {Path(file).suffix.lower() for file in files}):
            message = ("A WFDB recording needs both matching files: "
                       "select the .hea header and .dat signal.")
        return (error_card(message), gr.update(value=None, visible=False),
            "", gr.update(visible=True))


def fill_from_sample(name):
    record_key = DISPLAY_SAMPLES.get(name, name)
    m = SAMPLES.get(record_key, {})
    return (m.get("age"), m.get("sex", "M"),
            m.get("height_cm"), m.get("weight_kg"),
            bmi_caption(m.get("height_cm"), m.get("weight_kg")))


def bmi_caption(height, weight):
    if not height or not weight or height <= 0 or weight <= 0:
        return '<div class="bmi-caption">BMI unavailable</div>'
    bmi = weight / (height / 100) ** 2
    return f'<div class="bmi-caption">BMI {bmi:.1f}</div>'


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
# Gradio 6 moved `theme` and `css` from Blocks() to launch(). Pass them
# wherever this version expects them so the app looks the same on 4, 5 and 6.
_GR_MAJOR = int(gr.__version__.split(".")[0])
_blocks_kw = {"title": "AI powered sleep apnea screening"}
if _GR_MAJOR < 6:
    _blocks_kw.update(theme=THEME, css=CSS)

with gr.Blocks(**_blocks_kw) as demo:
    gr.HTML(hero())

    if LOAD_ERROR:
        gr.HTML(error_card(LOAD_ERROR))

    with gr.Row(equal_height=False):
        with gr.Column(scale=5):
            with gr.Group(elem_classes="bare"):
                gr.HTML('<div class="eyebrow">Recording</div>')
                with gr.Tabs():
                    with gr.Tab("Sample patients"):
                        sample_dd = gr.Dropdown(
                            choices=list(DISPLAY_SAMPLES.keys()) or ["(none bundled)"],
                            value=(list(DISPLAY_SAMPLES.keys()) or [None])[0],
                            label="Patient",
                            info="Real overnight recordings with clinician-scored AHI",
                            allow_custom_value=False,
                            filterable=False,
                            interactive=True)
                        sample_btn = gr.Button("Analyse recording",
                                               variant="primary", size="lg")
                    with gr.Tab("Upload"):
                        with gr.Row():
                            upload_hea = gr.File(label="Header (.hea)",
                                                 file_count="single",
                                                 file_types=[".hea"],
                                                 elem_classes="compact-upload")
                            upload_dat = gr.File(label="Signal (.dat)",
                                                 file_count="single",
                                                 file_types=[".dat"],
                                                 elem_classes="compact-upload")
                        csv_upload = gr.File(
                            label="Or upload a CSV of samples",
                            file_count="single",
                            file_types=[".csv", ".txt"],
                            elem_classes="compact-upload compact-csv")
                        upload_btn = gr.Button("Analyse recording",
                                               variant="primary", size="lg")

        with gr.Column(scale=4):
            with gr.Group(elem_classes=["bare", "patient-details"]):
                gr.HTML(f"""<div class="eyebrow">Patient details</div>
                  <div style="font-size:13.5px;color:{MUTED};line-height:1.6;
                       margin:-6px 0 16px 0">Optional. Age, sex and BMI are
                  established risk factors and refine the estimate.</div>""")
                with gr.Row():
                    age_in = gr.Number(label="Age", value=51, precision=0)
                    sex_in = gr.Radio(["M", "F"], value="M", label="Sex")
                with gr.Row():
                    h_in = gr.Number(label="Height (cm)", value=175, precision=0)
                    with gr.Column(scale=1, min_width=0):
                        w_in = gr.Number(label="Weight (kg)", value=102, precision=0)
                        bmi_note = gr.HTML(value=bmi_caption(175, 102))

    out_card = gr.HTML()
    out_placeholder = gr.HTML(value=plot_placeholder())
    out_plot = gr.Plot(value=None, show_label=False, container=False,
                       elem_classes="result-chart", visible=False)
    out_note = gr.HTML()

    sample_dd.change(fill_from_sample, sample_dd,
                     [age_in, sex_in, h_in, w_in, bmi_note])
    h_in.change(bmi_caption, [h_in, w_in], bmi_note)
    w_in.change(bmi_caption, [h_in, w_in], bmi_note)
    sample_btn.click(run_sample, [sample_dd, age_in, sex_in, h_in, w_in],
                     [out_card, out_plot, out_note, out_placeholder])
    upload_btn.click(run_upload,
             [upload_hea, upload_dat, csv_upload, age_in, sex_in,
              h_in, w_in],
            [out_card, out_plot, out_note, out_placeholder])

    gr.HTML(f"""<div style="font-family:{FONT};text-align:center;font-size:12.5px;
    color:{MUTED};padding:20px 0 4px;line-height:1.5">
            CNN BiLSTM on RR interval and ECG derived respiration series<br>
        PhysioNet Apnea ECG &middot; Grand Valley State University</div>""",
            elem_classes="footer-note")


if __name__ == "__main__":
    launch_kw = {}
    if _GR_MAJOR >= 6:
        launch_kw.update(theme=THEME, css=CSS)
    # Set share=True for a temporary public link (useful at a poster session).
    demo.launch(**launch_kw)
