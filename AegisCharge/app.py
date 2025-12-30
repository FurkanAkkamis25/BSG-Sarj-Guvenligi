import streamlit as st
import pandas as pd
import joblib
import os
import numpy as np

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="AegisCharge AI Panel", layout="wide")
st.title("🛡️ AegisCharge - AI Güvenlik Kalkanı")
st.markdown("*Elektrikli Araç Şarj İstasyonu - Anomali Tespit Sistemi*")

# --- DOSYALARI YÜKLE ---
@st.cache_resource
def sistem_dosyalarini_yukle():
    try:
        base_dir = os.path.dirname(__file__)
        model = joblib.load(os.path.join(base_dir, "rf_model.pkl"))
        scaler = joblib.load(os.path.join(base_dir, "scaler.pkl"))
        data = pd.read_csv(os.path.join(base_dir, "temiz_veri_v4.csv"))
        return model, scaler, data
    except Exception as e:
        st.error(f"Dosya yükleme hatası: {e}")
        return None, None, None

model, scaler, df = sistem_dosyalarini_yukle()

# --- MODELİN İSTEDİĞİ ÖZEL SÜTUNLAR ---
MODEL_SUTUNLARI = [
    "Voltage", 
    "Current_Import", 
    "Power_Import", 
    "SoC", 
    "Power_Ratio", 
    "SoC_Delta", 
    "Current_to_Voltage", 
    "Power_per_SoC"
]

def veri_hazirla(dataframe):
    data = dataframe.copy()
    
    # --- FEATURE ENGINEERING (Matematiksel Hesaplamalar) ---
    # Modelin beklediği ama ham veride olmayan sütunları üretiyoruz
    
    # 1. Current_to_Voltage
    data['Current_to_Voltage'] = data['Current_Import'] / data['Voltage'].replace(0, 1)

    # 2. Power_Ratio
    max_power = data['Power_Import'].max()
    if max_power == 0: max_power = 1
    data['Power_Ratio'] = data['Power_Import'] / max_power

    # 3. SoC_Delta
    data['SoC_Delta'] = data['SoC'].diff().fillna(0)
    
    # 4. Power_per_SoC
    data['Power_per_SoC'] = data['Power_Import'] / data['SoC'].replace(0, 1)
        
    # Sonsuz sayıları ve boşlukları temizle
    data = data.replace([np.inf, -np.inf], 0).fillna(0)
    
    # --- FİLTRELEME ---
    # Sadece modelin istediği 8 sütunu ayıralım
    sadece_gerekli_veri = data[MODEL_SUTUNLARI]
    
    return data, sadece_gerekli_veri

if df is not None:
    # Veriyi işle
    tum_veri, model_verisi = veri_hazirla(df)

    # --- KONTROL PANELİ ---
    st.info("👇 Simülasyonu Başlat: Zamanı İleri-Geri Sar")
    secilen_index = st.slider("Zaman Çizelgesi", 0, len(df)-1, 0)
    
    # Ekrana basılacak veriler
    anlik_ham = tum_veri.iloc[secilen_index]
    input_data = model_verisi.iloc[[secilen_index]]
    
    try:
        # --- YAPAY ZEKA TAHMİNİ ---
        input_scaled = scaler.transform(input_data)
        ai_prediction = model.predict(input_scaled)[0]
        ai_prob = model.predict_proba(input_scaled)[0][1]
        
        # --- SONUÇ GÖSTERGELERİ ---
        st.divider()
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("⚡ Voltaj", f"{anlik_ham['Voltage']:.2f} V")
        with col2:
            st.metric("🔌 Akım", f"{anlik_ham['Current_Import']:.2f} A")
        with col3:
            st.metric("🔋 Batarya (SoC)", f"%{anlik_ham['SoC']:.1f}")
        with col4:
            if ai_prediction == 1:
                st.error(f"🚨 SALDIRI VAR! (%{ai_prob*100:.0f})")
            else:
                st.success(f"✅ GÜVENLİ (%{(1-ai_prob)*100:.0f})")

        # --- GRAFİK ---
        st.subheader("📊 Canlı Sinyal Analizi")
        baslangic = max(0, secilen_index - 100) # Son 100 veriyi göster
        grafik_veri = tum_veri.iloc[baslangic : secilen_index + 1]
        
        # Grafikte hem voltajı hem akımı gösterelim (daha havalı olur)
        chart_data = grafik_veri[['Voltage', 'Current_Import']]
        st.line_chart(chart_data)

        # --- TABLO GÖRÜNÜMÜ (İSTEDİĞİN KISIM) ---
        st.divider()
        st.subheader("📋 Veri Seti ve Detaylar")
        
        with st.expander("Tüm Veri Setini Göster (Tıkla Aç/Kapa)", expanded=False):
            st.write("Aşağıdaki tablo, hem sensörlerden gelen ham veriyi hem de Yapay Zeka için hesaplanan özel verileri içerir.")
            # En son işlenmiş, tüm sütunları içeren veriyi gösteriyoruz
            st.dataframe(tum_veri)

    except Exception as e:
        st.error(f"Beklenmeyen bir hata oluştu: {e}")

else:
    st.warning("Dosyalar yüklenemedi. Lütfen klasörü kontrol et.")