import asyncio
import random
from dataclasses import dataclass
from typing import Dict, Any, List

from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class KaranlikProfilScenarioConfig(ScenarioConfig):
    """
    Karanlık Profil (Dark Profile Attack) senaryosu için konfigürasyon.
    
    Bu senaryo, log verilerinin toplanması ve kullanıcı profili oluşturulması
    durumunu simüle eder.
    """
    base_power_kw: float = 7.0  # Normal şarj gücü
    voltage_v: float = 400.0  # Şebeke voltajı
    battery_capacity_kwh: float = 60.0  # Batarya kapasitesi
    # Attack modda kullanılacak kullanıcı profilleri
    use_multiple_users: bool = True  # Çoklu kullanıcı simülasyonu


class KaranlikProfilScenario(ScenarioBase):
    """
    Karanlık Profil Saldırısı (Dark Profile Attack).
    
    Bu senaryo, EVCI sistemlerinde kullanılan CSMS'lerin veri gizliliği 
    zafiyetlerini hedef alan bir saldırıyı simüle eder. Saldırganlar, şarj 
    işlemlerini durdurmak yerine, kullanıcılara ait konum, zaman ve kimlik 
    bilgilerinin tutulduğu log verilerini ele geçirir ve analiz eder.
    
    Senaryo açıklaması:
    - Saldırgan, CSMS ve şarj istasyonları arasındaki veri akışını izleyerek
      log kayıtlarına erişim sağlar
    - Kullanıcıların kimlik, konum ve zaman bilgilerini içeren veriler
      toplanır ve analiz edilir
    - Ele geçirilen veriler ile kullanıcı profilleri (Dark Profiles) oluşturulur:
      * Günlük hareket rutinleri
      * Sık ziyaret edilen bölgeler
      * Görev yerleri
      * Sosyal çevre analizi
    
    Zafiyet:
    - Log kayıtlarının yeterince anonimleştirilmemesi
    - Zayıf şifreleme yöntemleri
    - CSMS erişim politikalarının yanlış yapılandırılması
    - Veri akışı izleme (network traffic monitoring)
    - API güvenliği eksiklikleri
    
    Saldırı adımları:
    1. Veri Erişimi: CSMS-CS veri akışını izleme, log kayıtlarına erişim
    2. Veri Sızdırma: Kullanıcı kimlik, konum, zaman bilgilerini toplama
    3. Profil Oluşturma: Hareket rutinleri, sık ziyaret edilen yerler analizi
    4. İstihbarat Kullanımı: Casusluk, hedefli saldırı, fiziksel takip
    
    Etki:
    - Veri gizliliği ihlali
    - Fiziksel güvenlik riski (kullanıcı konumlarının ifşası)
    - Ulusal istihbarat riski (kritik personel profilleri)
    - Kitle gözetim platformu haline gelme
    
    Modlar:
    - normal: Normal şarj akışı, tek kullanıcı (minimal log)
    - attack: Çoklu kullanıcı, detaylı log toplama, profil oluşturma simülasyonu
    """

    async def run_for_all_charge_points(
        self,
        cps: List[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:
        """
        Tüm charge point'ler için senaryo akışını çalıştırır.
        """
        # Boş liste kontrolü
        if not cps:
            print("[WARNING] run_for_all_charge_points boş liste ile çağrıldı!")
            return
        
        connector_id = 1
        tx_ids: Dict[str, int] = {}
        
        # Config değerlerini al
        base_power_kw = self.config.base_power_kw
        voltage_v = self.config.voltage_v
        battery_capacity_kwh = self.config.battery_capacity_kwh
        
        # Attack modunda çoklu kullanıcı simülasyonu
        # Her CP'ye farklı kullanıcı profili ata
        cp_user_profiles: Dict[str, Dict[str, Any]] = {}
        
        if mode == "attack":
            # CSMS'deki VALID_TAGS: YUNUS_TAG, AYSE_TAG, TEST123
            available_users = [
                {"id_tag": "YUNUS_TAG", "profile": "Critical_Personnel_Military"},
                {"id_tag": "AYSE_TAG", "profile": "Government_Official"},
                {"id_tag": "TEST123", "profile": "Energy_Sector_Employee"},
            ]
            
            print(f"[ATTACK] Karanlık Profil saldırısı başlatılıyor...")
            print(f"[ATTACK] Veri toplama ve profil oluşturma simülasyonu aktif.")
            print(f"[ATTACK] Hedef: Kullanıcı hareket rutinleri ve kritik personel profilleri")
            
            for i, cp in enumerate(cps):
                # Her CP'ye döngüsel olarak kullanıcı ata
                user = available_users[i % len(available_users)]
                cp_user_profiles[cp.id] = user
                print(f"[ATTACK] {cp.id}: {user['id_tag']} ({user['profile']})")
        else:
            # Normal modda tek kullanıcı
            for cp in cps:
                cp_user_profiles[cp.id] = {
                    "id_tag": "YUNUS_TAG",
                    "profile": "Regular_User"
                }

        # -----------------------------
        # 1) CP → CS: StatusNotification(Available)
        # -----------------------------
        print(f"[INFO] {len(cps)} CP için StatusNotification(Available) gönderiliyor...")
        for cp in cps:
            try:
                await cp.send_status_notification(
                    connector_id=connector_id,
                    status="Available",
                )
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"[ERROR] {cp.id} StatusNotification hatası: {e}")
                continue

        # -----------------------------
        # 2) Authorize → Accepted mi?
        # -----------------------------
        print(f"[INFO] {len(cps)} CP için Authorize işlemi başlatılıyor...")
        for cp in cps:
            try:
                # Her CP için atanmış kullanıcıyı kullan
                user_profile = cp_user_profiles[cp.id]
                id_tag = user_profile["id_tag"]
                
                if mode == "attack":
                    print(f"[ATTACK] {cp.id}: Kullanıcı {id_tag} için veri toplama başlatıldı")

                status = await cp.send_authorize(id_tag)

                if status != "Accepted":
                    print(f"[!] {cp.id}: IDTag reddedildi → Şarj oturumu başlamayacak.")
                    continue

                # "Preparing" durumuna geç
                await cp.send_status_notification(
                    connector_id=connector_id,
                    status="Preparing",
                )
                await asyncio.sleep(0.1)

                # -----------------------------
                # 3) StartTransaction
                # -----------------------------
                start_res = await cp.send_start_transaction(
                    connector_id=connector_id,
                    id_tag=id_tag,
                    meter_start=0,
                )
                tx_ids[cp.id] = start_res.transaction_id
                
                if mode == "attack":
                    print(f"[ATTACK] {cp.id}: Transaction {start_res.transaction_id} - "
                          f"Kullanıcı: {id_tag}, Zaman: {asyncio.get_event_loop().time():.0f}s, "
                          f"Profil: {user_profile['profile']}")

                # "Charging" durumuna geç
                await cp.send_status_notification(
                    connector_id=connector_id,
                    status="Charging",
                )
            except Exception as e:
                print(f"[ERROR] {cp.id} Authorize/StartTransaction hatası: {e}")
                continue
        
        print(f"[INFO] {len(tx_ids)} transaction başlatıldı.")
        
        if mode == "attack":
            print(f"[ATTACK] LOG SIZMA SİMÜLASYONU: Tüm transaction verileri toplanıyor...")
            print(f"[ATTACK] Veri noktaları: Kullanıcı ID, Zaman, Konum (CP ID), Transaction süreleri")

        # -----------------------------
        # 4) MeterValues DÖNGÜSÜ
        # -----------------------------
        soc_state: Dict[str, float] = {cp.id: 20.0 for cp in cps}
        
        print(f"[INFO] MeterValues döngüsü başlatılıyor (duration={duration})...")
        
        for step in range(1, duration + 1):
            for cp in cps:
                try:
                    tx_id = tx_ids.get(cp.id)
                    if tx_id is None:
                        continue
                    
                    # Normal güç değeri (her iki modda da aynı - saldırı veri toplamada)
                    power_kw = base_power_kw + random.uniform(-0.3, 0.3)
                    
                    # P = V * I → I = P / V
                    current_a = (power_kw * 1000) / voltage_v
                    
                    # SoC HESABI
                    dt_hours = 1.0 / 3600.0
                    energy_kwh = max(power_kw, 0.0) * dt_hours
                    delta_soc = (energy_kwh / battery_capacity_kwh) * 100.0
                    
                    soc = soc_state.get(cp.id, 20.0)
                    soc = min(100.0, soc + delta_soc)
                    soc_state[cp.id] = soc

                    # MeterValues gönder
                    await cp.send_meter_values(
                        connector_id=connector_id,
                        power_kw=power_kw,
                        current_a=current_a,
                        voltage_v=voltage_v,
                        transaction_id=tx_id,
                        soc_percent=soc,
                    )
                except Exception as e:
                    print(f"[ERROR] {cp.id} MeterValues hatası (step {step}): {e}")
                    continue

            # Her adım arasında 1 saniye bekle
            await asyncio.sleep(1)

        # -----------------------------
        # 5) StopTransaction + Finishing + Available
        # -----------------------------
        print(f"[INFO] Simülasyon sonu: Transaction'lar durduruluyor...")
        
        if mode == "attack":
            print(f"[ATTACK] PROFİL OLUŞTURMA SİMÜLASYONU:")
            print(f"[ATTACK] ═══════════════════════════════════════════════════════")
            
            # Her kullanıcı için profil özeti
            user_stats: Dict[str, List[str]] = {}
            for cp_id, user_profile in cp_user_profiles.items():
                id_tag = user_profile["id_tag"]
                profile_type = user_profile["profile"]
                
                if id_tag not in user_stats:
                    user_stats[id_tag] = []
                user_stats[id_tag].append(f"{cp_id} ({profile_type})")
            
            for id_tag, locations in user_stats.items():
                print(f"[ATTACK] ├─ Kullanıcı: {id_tag}")
                print(f"[ATTACK] │  ├─ Toplam şarj noktası: {len(locations)}")
                print(f"[ATTACK] │  ├─ Konumlar: {', '.join(locations)}")
                print(f"[ATTACK] │  ├─ Süre: {duration} saniye")
                print(f"[ATTACK] │  └─ Pattern: Düzenli kullanım tespit edildi")
            
            print(f"[ATTACK] ═══════════════════════════════════════════════════════")
            print(f"[ATTACK] SONUÇ: {len(user_stats)} kullanıcı profili oluşturuldu")
            print(f"[ATTACK] RİSK: Hareket rutinleri, konum bilgileri ifşa edildi")
            print(f"[ATTACK] KULLANIM: Casusluk, hedefli saldırı, fiziksel takip")
        
        for cp in cps:
            try:
                tx_id = tx_ids.get(cp.id)
                if not tx_id:
                    continue

                # Finishing
                await cp.send_status_notification(
                    connector_id=connector_id,
                    status="Finishing",
                )

                await cp.send_stop_transaction(
                    transaction_id=tx_id,
                    meter_stop=0,
                )
            except Exception as e:
                print(f"[ERROR] {cp.id} StopTransaction hatası: {e}")
                continue

            # tekrar Available
            await cp.send_status_notification(
                connector_id=connector_id,
                status="Available",
            )

    # -----------------------------
    # LABEL DÖNÜŞÜ
    # -----------------------------
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        """
        Her event için anomaly label'ı belirler.
        """
        if mode == "normal":
            return "normal"

        # Attack modunda:
        message_type = event.get("message_type") or event.get("ocpp_action")

        # Authorize ve StartTransaction event'leri veri toplama için kritik
        # (Kullanıcı kimlik ve konum bilgileri)
        if message_type in ["Authorize", "StartTransaction"]:
            return "dark_profile_attack"
        
        # Transaction event'leri profil oluşturma için kullanılır
        if message_type == "StopTransaction":
            return "dark_profile_attack"

        # MeterValues ve diğer event'ler bağlamsal veri
        return "attack_meta"


# ----------------------------------------------------------------------
# run_simulation.py tarafından çağrılan giriş noktası
# ----------------------------------------------------------------------
async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
    cp_list: list[str] | None = None,
) -> None:
    """
    run_simulation.py'nin beklediği senaryo giriş noktası.
    """
    config = KaranlikProfilScenarioConfig(
        name="karanlik_profil",
        base_power_kw=7.0,
        voltage_v=400.0,
        battery_capacity_kwh=60.0,
        use_multiple_users=True,
    )
    scenario = KaranlikProfilScenario(config=config)

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
        cp_list=cp_list,
    )

