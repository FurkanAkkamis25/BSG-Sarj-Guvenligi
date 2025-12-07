# ui/ocpp_control_panel.py

import subprocess
import os
from pathlib import Path

import streamlit as st

# === Proje kök dizini (run_simulation.py'nin olduğu yer) ===
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# -----------------------------------------------------------
# Yardımcı fonksiyonlar
# -----------------------------------------------------------
def init_session_state():
    """İlk açılışta session_state içini hazırlayalım."""
    if "cp_states" not in st.session_state:
        # Örnek CP listesi – istersen burayı config dosyasından okuyabilirsin
        st.session_state.cp_states = {
            "CP_001": "Active",
            "CP_002": "Offline",
            "CP_003": "Active",
            "CP_004": "Offline",
        }

    if "sim_proc" not in st.session_state:
        st.session_state.sim_proc = None

    if "last_logs" not in st.session_state:
        st.session_state.last_logs = ""


def start_simulation_for_cp(cp_id: str, scenario: str, mode: str, duration: int):
    """Seçilen CP için run_simulation.py'yi ayrı bir process olarak çalıştırır."""

    # Zaten çalışan bir process varsa tekrar başlatma
    proc = st.session_state.sim_proc
    if proc and proc.poll() is None:
        st.warning("Zaten çalışan bir simülasyon var. Önce durdurmalısın.")
        return

    # Çalışma klasörü proje kökü olsun
    cwd = str(PROJECT_ROOT)

    # Log dosyası adı (CP + senaryo + mod)
    log_name = f"{scenario}_{mode}_{cp_id}.csv"
    log_path = PROJECT_ROOT / "logs" / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "py",  # Windows python launcher
        str(PROJECT_ROOT / "run_simulation.py"),
        "--scenario",
        scenario,
        "--mode",
        mode,
        "--duration",
        str(duration),
        "--stations",
        "1",
        "--output",
        str(log_path),
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    st.session_state.sim_proc = proc
    st.success(f"Simülasyon başlatıldı (CP={cp_id}, PID={proc.pid}). Log: {log_path.name}")


def stop_simulation():
    """Çalışan simülasyon process'ini durdurur."""
    proc = st.session_state.sim_proc
    if not proc:
        st.info("Şu anda çalışan bir simülasyon yok.")
        return

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    st.session_state.sim_proc = None
    st.success("Simülasyon durduruldu.")


def read_process_logs():
    """Çocuk process'ten gelen stdout'u oku."""
    proc = st.session_state.sim_proc
    if not proc or proc.stdout is None:
        return ""

    lines = []
    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
    except Exception:
        pass

    return "".join(lines)


# -----------------------------------------------------------
# UI – OCPP Control Panel
# -----------------------------------------------------------
def main():
    st.set_page_config(
        page_title="OCPP Control Panel",
        page_icon="⚡",
        layout="wide",
    )

    init_session_state()

    st.title("⚡ OCPP Charge Point Control Panel")

    tabs = st.tabs(["🔍 Charge Points", "🧪 Simulator", "📜 Logs"])

    # -------------------------------------------------------
    # TAB 1: Charge Point Monitoring
    # -------------------------------------------------------
    with tabs[0]:
        st.subheader("Charge Point Status Monitor")

        st.markdown(
            "Burada CP'lerin **Active / Offline** durumlarını yönetiyorsun. "
            "Simulator sekmesinde bir CP seçildiğinde, sadece **Active** olanlara bağlanılabilecek."
        )

        cols = st.columns([2, 2, 2, 2])

        with cols[0]:
            st.markdown("**Charge Point**")
        with cols[1]:
            st.markdown("**Status**")
        with cols[2]:
            st.markdown("**Değiştir**")
        with cols[3]:
            st.markdown("**Açıklama**")

        for cp_id, status in st.session_state.cp_states.items():
            col1, col2, col3, col4 = st.columns([2, 2, 2, 2])

            with col1:
                st.text(cp_id)

            with col2:
                if status == "Active":
                    st.markdown(":green_circle: **Active / Available**")
                else:
                    st.markdown(":red_circle: **Offline / Unavailable**")

            with col3:
                new_status = st.selectbox(
                    f"status_{cp_id}",
                    options=["Active", "Offline"],
                    index=0 if status == "Active" else 1,
                    key=f"status_select_{cp_id}",
                )
                # Dropdown değişmişse güncelle
                if new_status != status:
                    st.session_state.cp_states[cp_id] = new_status

            with col4:
                if status == "Active":
                    st.caption("Bu CP üzerinden simülasyon başlatılabilir.")
                else:
                    st.caption("Bu CP seçilirse simulator hata verecek.")

    # -------------------------------------------------------
    # TAB 2: Simulator
    # -------------------------------------------------------
    with tabs[1]:
        st.subheader("OCPP Scenario Simulator")

        st.markdown(
            "Bu ekranda bir CP seçip senaryoyu başlatırsın. "
            "Seçilen CP **Active değilse** simülasyon başlatılmaz ve uyarı alırsın."
        )

        left, right = st.columns([2, 3])

        with left:
            cp_list = list(st.session_state.cp_states.keys())
            selected_cp = st.selectbox("Charge Point", cp_list, index=0)

            # Seçili CP'nin durumu
            cp_status = st.session_state.cp_states[selected_cp]

            if cp_status == "Active":
                st.success(f"{selected_cp} şu anda **Active / Available**.")
            else:
                st.error(f"{selected_cp} şu anda **Offline / Unavailable**. Bu CP ile simülasyon başlatılamaz.")

            scenario = st.selectbox(
                "Scenario",
                options=["dalgali_yuk"],  # şimdilik tek senaryo
            )

            mode = st.selectbox(
                "Mode",
                options=["normal", "attack"],
                index=1,
            )

            duration = st.slider("Duration (seconds)", min_value=5, max_value=600, value=60, step=5)

            st.markdown("---")
            col_a, col_b = st.columns(2)

            with col_a:
                if st.button("▶ Start Simulation"):
                    if cp_status != "Active":
                        st.error(
                            f"{selected_cp} Offline olduğu için simülasyon başlatılmadı. "
                            "Önce Charge Points sekmesinden durumu **Active** yapmalısın."
                        )
                    else:
                        start_simulation_for_cp(
                            cp_id=selected_cp,
                            scenario=scenario,
                            mode=mode,
                            duration=duration,
                        )

            with col_b:
                if st.button("⏹ Stop Simulation"):
                    stop_simulation()

        with right:
            st.markdown("#### Process Output (run_simulation.py)")
            if st.button("Logları Yenile"):
                logs = read_process_logs()
                if logs:
                    st.session_state.last_logs += logs

            if st.session_state.last_logs:
                st.code(st.session_state.last_logs, language="text")
            else:
                st.info("Henüz gösterilecek log yok.")

    # -------------------------------------------------------
    # TAB 3: Logs – sadece bilgi
    # -------------------------------------------------------
    with tabs[2]:
        st.subheader("Log Files Overview")

        st.markdown(
            f"""
            - Tüm log dosyaları şu klasörde tutuluyor:\n
            `{PROJECT_ROOT / "logs"}`\n
            - Her simülasyon için:\n
              - Birleşik dataset CSV\n
              - `*_meter_values.csv`\n
              - `*_status_notifications.csv`\n
              - `*_heartbeats.csv`\n
              - `*_transactions.csv`\n
              - `*_events_raw.csv`
            """
        )


if __name__ == "__main__":
    main()
