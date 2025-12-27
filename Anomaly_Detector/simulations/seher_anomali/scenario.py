import asyncio
import math
import random
from dataclasses import dataclass
from typing import Dict, Any, List

from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class SeherAnomaliScenarioConfig(ScenarioConfig):
    pass


class SeherAnomaliScenario(ScenarioBase):

    async def run_for_all_charge_points(
        self,
        cps: List[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:

        connector_id = 1
        tx_ids: Dict[str, int] = {}

        for cp in cps:
            await cp.send_status_notification(
                connector_id=connector_id,
                status="Available",
            )
            await asyncio.sleep(0.1)

        for idx, cp in enumerate(cps, start=1):
            id_tag = "YUNUS_TAG"

            status = await cp.send_authorize(id_tag)

            if status != "Accepted":
                continue

            await cp.send_status_notification(
                connector_id=connector_id,
                status="Preparing",
            )
            await asyncio.sleep(0.1)

            start_res = await cp.send_start_transaction(
                connector_id=connector_id,
                id_tag=id_tag,
                meter_start=0,
            )
            tx_ids[cp.id] = start_res.transaction_id

            await cp.send_status_notification(
                connector_id=connector_id,
                status="Charging",
            )

        voltage_v = 230.0
        base_power_kw = 11.0
        
        battery_capacity_kwh = 60.0
        soc_state: Dict[str, float] = {cp.id: 20.0 for cp in cps}

        for step in range(1, duration + 1):
            for cp in cps:
                tx_id = tx_ids.get(cp.id)
                if not tx_id:
                    continue

                if mode == "normal":
                    current_voltage = voltage_v + random.uniform(-2.0, 2.0)
                    power_kw = base_power_kw + random.uniform(-0.1, 0.1)
                else:
                    if random.random() < 0.3:
                        current_voltage = voltage_v * 0.7 
                    else:
                        current_voltage = voltage_v + random.uniform(-5.0, 5.0)
                    
                    power_kw = base_power_kw + random.uniform(-0.5, 0.5)

                current_a = (power_kw * 1000) / current_voltage

                dt_hours = 1.0 / 3600.0
                energy_kwh = max(power_kw, 0.0) * dt_hours
                delta_soc = (energy_kwh / battery_capacity_kwh) * 100.0

                soc = soc_state.get(cp.id, 20.0)
                soc = min(100.0, soc + delta_soc)
                soc_state[cp.id] = soc

                await cp.send_meter_values(
                    connector_id=connector_id,
                    power_kw=power_kw,
                    current_a=current_a,
                    voltage_v=current_voltage,
                    transaction_id=tx_id,
                    soc_percent=soc,
                )

            await asyncio.sleep(1)

        for cp in cps:
            tx_id = tx_ids.get(cp.id)
            if not tx_id:
                continue

            await cp.send_status_notification(
                connector_id=connector_id,
                status="Finishing",
            )

            await cp.send_stop_transaction(
                transaction_id=tx_id,
                meter_stop=0,
            )

            await cp.send_status_notification(
                connector_id=connector_id,
                status="Available",
            )

    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        if mode == "normal":
            return "normal"

        message_type = event.get("message_type") or event.get("ocpp_action")

        if message_type == "MeterValues":
            return "voltage_sag_attack"

        return "attack_meta"


async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
    cp_list: list[str] | None = None,
) -> None:
    config = SeherAnomaliScenarioConfig(name="seher_anomali")
    scenario = SeherAnomaliScenario(config=config)

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
        cp_list=cp_list,
    )
