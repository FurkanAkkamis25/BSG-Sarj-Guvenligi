# veri_uret.py
import pandas as pd
import numpy as np
import time

# 100 satırlık rastgele bir veri seti oluşturalım
# Sanki şarj istasyonundan veri akıyormuş gibi
data = {
    'Timestamp': pd.date_range(start='2025-11-07 14:00:00', periods=100, freq='S'), # Zaman
    'Voltage': np.random.uniform(215, 225, 100),  # 215-225 Volt arası normal değerler
    'Current_Import': np.random.uniform(10, 16, 100), # 10-16 Amper arası
    'Label': np.zeros(100) # Hepsi şimdilik "0" (Normal) olsun
}

# Aralara 3-5 tane "Saldırı" (Label = 1) serpiştirelim
data['Label'][20:25] = 1 # 20. ve 25. saniyeler arası saldırı var!
data['Voltage'][20:25] = 180 # Voltaj düşmüş (Saldırı belirtisi)

df = pd.DataFrame(data)
df.to_csv("test_verisi.csv", index=False)
print("Sahte veri 'test_verisi.csv' adıyla oluşturuldu!")