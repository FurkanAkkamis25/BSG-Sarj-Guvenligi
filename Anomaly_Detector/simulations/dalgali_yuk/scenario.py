# simulations/dalgali_yuk/scenario.py
import asyncio
import math
import random
from dataclasses import dataclass

from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class DalgaliYukScenarioConfig(ScenarioConfig):
    """
    Dalgalı Yük Senaryosu için ek ayarlar gerekiyorsa buraya eklenebilir.
    Şimdilik ScenarioConfig ile aynı.
    """
    pass


class DalgaliYukScenario(ScenarioBase):
    """
    Dalgalı Yük Saldırısı (Oscillatory Load Attack) senaryosu.

    - mode="normal"  -> tüm MeterValues normal yük profili
    - mode="attack"  -> tüm MeterValues dalgalı/osilasyonlu yük profili
    (İstersen ileride yarısını normal yarısını attack yapacak şekilde genişletebiliriz.)
    """

    async def run_for_all_charge_points(
        self,
        cps: list[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:
        """
        Burada senaryonun ana akışını tanımlıyoruz:

        1. Her CP için:
            - Authorize (RFID/kullanıcı kimliği)
            - StartTransaction
        2. duration boyunca periyodik MeterValues gönder
            - normal modda düzgün yük
            - attack modda dalgalı yük
        3. En sonunda StopTransaction
        """
        connector_id = 1

        # 1) Her CP için Authorize + StartTransaction
        tx_ids: dict[str, int] = {}
        for idx, cp in enumerate(cps, start=1):
            id_tag = f"TAG_NORMAL_{idx:03d}"

            # Kimlik doğrulama (ileride kimlik hırsızlığı senaryosunda burayı manipüle edeceğiz)
            await cp.send_authorize(id_tag)

            # Şarj oturumu başlat
            start_res = await cp.send_start_transaction(
                connector_id=connector_id,
                id_tag=id_tag,
                meter_start=0,
            )
            tx_ids[cp.id] = start_res.transaction_id

        # 2) MeterValues döngüsü
        base_power_kw = 7.0      # ortalama şarj gücü
        attack_delta_kw = 3.0    # saldırı dalgalanma genliği
        voltage_v = 400.0        # trifaze varsayım

        for step in range(1, duration + 1):
            for cp in cps:
                # Normal yük profili
                if mode == "normal":
                    # etrafında hafif oynayan sabit güç
                    noise = random.uniform(-0.4, 0.4)
                    power_kw = base_power_kw + noise

                # Dalgalı Yük Saldırısı
                else:  # mode == "attack"
                    # basit sinüs tabanlı osilasyon
                    # step'i saniye gibi düşün: f = 0.25 Hz => 4 sn’de bir tam tur
                    freq_hz = 0.25
                    angle = 2 * math.pi * freq_hz * step
                    osc = math.sin(angle)

                    power_kw = base_power_kw + attack_delta_kw * osc
                    # biraz rastgelelik ekle
                    power_kw += random.uniform(-0.5, 0.5)
                    # negatif olmasın
                    power_kw = max(power_kw, 0.1)

                current_a = (power_kw * 1000) / voltage_v

                tx_id = tx_ids.get(cp.id)
                await cp.send_meter_values(
                    connector_id=connector_id,
                    power_kw=power_kw,
                    current_a=current_a,
                    voltage_v=voltage_v,
                    transaction_id=tx_id,
                )

            # her adım arasında küçük gecikme
            await asyncio.sleep(0.2)

        # 3) StopTransaction
        for cp in cps:
            tx_id = tx_ids.get(cp.id)
            if tx_id is not None:
                await cp.send_stop_transaction(
                    transaction_id=tx_id,
                    meter_stop=0,
                )

    # ------------------------------------------------------------------
    # Labellama (anomali etiketi)
    # ------------------------------------------------------------------
    def get_label_for_event(self, event: dict, mode: str) -> str:
        """
        Bu senaryoda:
        - normal modda tüm olaylar "normal"
        - attack modda:
            - MeterValues için: "oscillatory_load_attack"
            - Diğer olaylar için: "attack_meta" (başlangıç/bitiş vs.)
        """
        if mode == "normal":
            return "normal"

        # mode == "attack"
        if event.get("message_type") == "MeterValues":
            return "oscillatory_load_attack"

        return "attack_meta"


# ----------------------------------------------------------------------
# run_simulation.py'nin çağırdığı fonksiyon
# ----------------------------------------------------------------------
async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
):
    """
    run_simulation.py bu fonksiyonu çağırıyor.

    Örnek:
        python run_simulation.py --scenario dalgali_yuk --mode attack --duration 120 --stations 2
    """
    config = DalgaliYukScenarioConfig(name="dalgali_yuk")
    scenario = DalgaliYukScenario(config)
    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
    )
