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

    if "multi_mode" not in st.session_state:
        st.session_state.multi_mode = False

    if "sim_proc" not in st.session_state:
        st.session_state.sim_proc = None

    if "last_logs" not in st.session_state:
        st.session_state.last_logs = ""


def start_simulation_for_cp(cp_id: str, scenario: str, mode: str, duration: int, stations: int = 1, cp_list: list[str] | None = None):
    """Seçilen CP için run_simulation.py'yi ayrı bir process olarak çalıştırır.
    
    Parametreler:
        cp_id: Tek CP modunda kullanılır (geriye uyumlu)
        cp_list: Çoklu CP modunda kullanılır (yeni özellik)
    """

    # Zaten çalışan bir process varsa tekrar başlatma
    proc = st.session_state.sim_proc
    if proc and proc.poll() is None:
        st.warning("Zaten çalışan bir simülasyon var. Önce durdurmalısın.")
        return

    # Çalışma klasörü proje kökü olsun
    cwd = str(PROJECT_ROOT)

    # Log dosyası adı (CP + senaryo + mod)
    # run_simulation.py göreli path'i logs/ocpp/ altına koyuyor
    if stations > 1:
        log_name = f"{scenario}_{mode}_{stations}stations.csv"
    else:
        log_name = f"{scenario}_{mode}_{cp_id}.csv"
    # Göreli path gönder (run_simulation.py logs/ocpp/ altına koyacak)
    log_path = log_name

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
        str(stations),
        "--output",
        str(log_path),
    ]
    
    # Eğer cp_list varsa ekle
    if cp_list:
        cmd.extend(["--cp-list"] + cp_list)

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    st.session_state.sim_proc = proc
    if stations > 1:
        st.success(f"Simülasyon başlatıldı ({stations} istasyon, PID={proc.pid}). Log: {log_path}")
    else:
        st.success(f"Simülasyon başlatıldı (CP={cp_id}, PID={proc.pid}). Log: {log_path}")


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
            # Çoklu İstasyon Modu butonu
            col_multi, col_info = st.columns([1, 2])
            with col_multi:
                if st.button("🔢 Çoklu İstasyon Modu", help="50 şarj istasyonu oluşturur"):
                    # 50 CP oluştur
                    for i in range(1, 51):
                        cp_id = f"CP_{i:03d}"
                        if cp_id not in st.session_state.cp_states:
                            st.session_state.cp_states[cp_id] = "Active"
                    st.session_state.multi_mode = True
                    st.rerun()
            
            with col_info:
                if st.session_state.multi_mode:
                    st.info("Çoklu mod aktif")
            
            # Mod seçimi
            simulation_mode = st.radio(
                "Simülasyon Modu",
                options=["Tek İstasyon", "Çoklu İstasyon"],
                index=0 if not st.session_state.multi_mode else 1,
                help="Tek istasyon: Bir CP seçin. Çoklu istasyon: Birden fazla CP seçin."
            )
            
            is_multi = (simulation_mode == "Çoklu İstasyon")
            
            # Çoklu mod seçildiyse ve yeterli CP yoksa otomatik oluştur
            if is_multi:
                current_cp_count = len(st.session_state.cp_states)
                if current_cp_count < 50:
                    # 50 CP'ye tamamla
                    for i in range(1, 51):
                        cp_id = f"CP_{i:03d}"
                        if cp_id not in st.session_state.cp_states:
                            st.session_state.cp_states[cp_id] = "Active"
                    st.session_state.multi_mode = True
            
            cp_list = sorted(list(st.session_state.cp_states.keys()))  # Sıralı liste
            active_cp_list = [cp for cp in cp_list if st.session_state.cp_states[cp] == "Active"]
            
            if is_multi:
                # Çoklu seçim
                selected_cps = st.multiselect(
                    "Charge Points (Çoklu Seçim)",
                    options=cp_list,
                    default=active_cp_list[:min(10, len(active_cp_list))] if active_cp_list else [],
                    help="Birden fazla CP seçebilirsiniz. Seçilen CP sayısı kadar istasyon simüle edilir."
                )
                
                if selected_cps:
                    # Seçilen CP'lerin durumunu kontrol et
                    inactive_cps = [cp for cp in selected_cps if st.session_state.cp_states[cp] != "Active"]
                    if inactive_cps:
                        st.warning(f"⚠️ Şu CP'ler Offline: {', '.join(inactive_cps)}")
                    
                    active_selected = [cp for cp in selected_cps if st.session_state.cp_states[cp] == "Active"]
                    stations_count = len(active_selected)
                    st.info(f"📊 {stations_count} aktif istasyon seçildi")
                else:
                    stations_count = 0
                    st.warning("En az bir CP seçmelisiniz.")
            else:
                # Tek seçim
                selected_cp = st.selectbox("Charge Point", cp_list, index=0)
                selected_cps = [selected_cp]
                
                # Seçili CP'nin durumu
                cp_status = st.session_state.cp_states[selected_cp]

                if cp_status == "Active":
                    st.success(f"{selected_cp} şu anda **Active / Available**.")
                else:
                    st.error(f"{selected_cp} şu anda **Offline / Unavailable**. Bu CP ile simülasyon başlatılamaz.")
                
                stations_count = 1 if cp_status == "Active" else 0

            scenario = st.selectbox(
                "Scenario",
                options=["dalgali_yuk", "sebeke_istikrarsizligi"],  # İki senaryo mevcut
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
                    if is_multi:
                        if not selected_cps:
                            st.error("En az bir CP seçmelisiniz.")
                        elif stations_count == 0:
                            st.error("Seçilen CP'lerden hiçbiri Active değil. Önce Charge Points sekmesinden durumu **Active** yapmalısın.")
                        else:
                            start_simulation_for_cp(
                                cp_id=selected_cps[0] if selected_cps else "CP_001",
                                scenario=scenario,
                                mode=mode,
                                duration=duration,
                                stations=stations_count,
                                cp_list=selected_cps,  # Seçilen CP listesini gönder
                            )
                    else:
                        if stations_count == 0:
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
                                stations=1,
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
