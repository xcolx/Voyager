import os.path
import time
import warnings
from typing import SupportsFloat, Any, Tuple, Dict

import requests
import json

import gymnasium as gym
from gymnasium.core import ObsType

import voyager.utils as U

from .minecraft_launcher import MinecraftInstance
from .process_monitor import SubprocessMonitor


class EnvError(RuntimeError):
    """The Minecraft side failed (mineflayer crashed, the bot got kicked, a request timed out).
    Recover with a soft reset, which rejoins without touching the world."""


class VoyagerEnv(gym.Env):
    def __init__(
        self,
        mc_port=None,
        azure_login=None,
        server_host="http://127.0.0.1",
        server_port=3000,
        request_timeout=600,
        step_timeout=None,
        log_path="./logs",
    ):
        if not mc_port and not azure_login:
            raise ValueError("Either mc_port or azure_login must be specified")
        if mc_port and azure_login:
            warnings.warn(
                "Both mc_port and mc_login are specified, mc_port will be ignored"
            )
        self.mc_port = mc_port
        self.azure_login = azure_login
        self.server = f"{server_host}:{server_port}"
        self.server_port = server_port
        self.request_timeout = request_timeout
        # abort generated code inside mineflayer before the http request itself times out
        self.step_timeout = step_timeout or max(request_timeout - 60, request_timeout // 2)
        self.log_path = log_path
        self.mineflayer = self.get_mineflayer_process(server_port)
        if azure_login:
            self.mc_instance = self.get_mc_instance()
        else:
            self.mc_instance = None
        self.has_reset = False
        self.reset_options = None
        self.connected = False
        self.server_paused = False

    def get_mineflayer_process(self, server_port):
        U.f_mkdir(self.log_path, "mineflayer")
        file_path = os.path.abspath(os.path.dirname(__file__))
        return SubprocessMonitor(
            commands=[
                "node",
                U.f_join(file_path, "mineflayer/index.js"),
                str(server_port),
            ],
            name="mineflayer",
            ready_match=r"Server started on port (\d+)",
            log_path=U.f_join(self.log_path, "mineflayer"),
        )

    def get_mc_instance(self):
        print("Creating Minecraft server")
        U.f_mkdir(self.log_path, "minecraft")
        return MinecraftInstance(
            **self.azure_login,
            mineflayer=self.mineflayer,
            log_path=U.f_join(self.log_path, "minecraft"),
        )

    def check_process(self):
        if self.mc_instance and not self.mc_instance.is_running:
            # if self.mc_instance:
            #     self.mc_instance.check_process()
            #     if not self.mc_instance.is_running:
            print("Starting Minecraft server")
            self.mc_instance.run()
            self.mc_port = self.mc_instance.port
            self.reset_options["port"] = self.mc_instance.port
            print(f"Server started on port {self.reset_options['port']}")
        retry = 0
        while not self.mineflayer.is_running:
            print("Mineflayer process has exited, restarting")
            self.mineflayer.run()
            if not self.mineflayer.is_running:
                retry += 1
                if retry > 3:
                    raise EnvError("Mineflayer process failed to start")
                else:
                    continue
            print(self.mineflayer.ready_line)
            try:
                res = requests.post(
                    f"{self.server}/start",
                    # a bot that got kicked while the server was paused cannot unpause it, so the new one does
                    json={**self.reset_options, "unpause": self.server_paused},
                    timeout=self.request_timeout,
                )
            except requests.exceptions.RequestException as e:
                # the bot got disconnected while joining, mineflayer exits and is restarted
                retry += 1
                if retry > 3:
                    raise EnvError(f"Bot failed to join: {e}") from e
                print(f"Bot failed to join ({e}), retrying")
                self.mineflayer.stop()
                continue
            if res.status_code == 200:
                self.server_paused = False
            else:
                self.mineflayer.stop()
                raise EnvError(f"Minecraft server reply with code {res.status_code}")
            return res.json()

    def step(
        self,
        code: str,
        programs: str = "",
    ) -> Tuple[ObsType, SupportsFloat, bool, bool, Dict[str, Any]]:
        if not self.has_reset:
            raise RuntimeError("Environment has not been reset yet")
        try:
            self.check_process()
            self.unpause()
            res = requests.post(
                f"{self.server}/step",
                json={"code": code, "programs": programs},
                timeout=self.request_timeout,
            )
            if res.status_code != 200:
                raise EnvError(f"Failed to step Minecraft server: {res.status_code}")
            events = json.loads(res.json())
        except requests.exceptions.RequestException as e:
            # the bot got disconnected or the code blocked mineflayer, restart it on the next step
            if isinstance(e, requests.exceptions.ConnectionError):
                self.mineflayer.wait_for_exit(timeout=10)
            else:
                self.mineflayer.stop()
            raise EnvError(f"Mineflayer request failed: {e}") from e
        self.pause()
        if res.headers.get("X-Voyager-Restart"):
            # the step was aborted and its code may still run, rejoin with a fresh mineflayer
            print("Step was aborted, restarting mineflayer")
            self.mineflayer.stop()
        return events

    def render(self):
        raise NotImplementedError("render is not implemented")

    def reset(
        self,
        *,
        seed=None,
        options=None,
    ) -> Tuple[ObsType, Dict[str, Any]]:
        if options is None:
            options = {}

        for key in ["inventory", "equipment", "position", "spread"]:
            if options.get(key):
                warnings.warn(f"reset option {key} is no longer supported and ignored")
        if options.get("mode", "soft") == "hard":
            warnings.warn(
                "hard reset is no longer supported, start from a new world instead. Doing a soft reset"
            )

        # a reset only rejoins, the server keeps inventory, equipment and position
        self.reset_options = {
            "port": self.mc_port,
            "waitTicks": options.get("wait_ticks", 5),
            "stepTimeout": self.step_timeout,
        }

        self.unpause()
        self.mineflayer.stop()
        time.sleep(1)  # wait for mineflayer to exit

        returned_data = self.check_process()
        self.has_reset = True
        self.connected = True
        self.pause()
        return json.loads(returned_data)

    def close(self):
        self.unpause()
        if self.connected:
            res = requests.post(f"{self.server}/stop")
            if res.status_code == 200:
                self.connected = False
        if self.mc_instance:
            self.mc_instance.stop()
        self.mineflayer.stop()
        return not self.connected

    def pause(self):
        if self.mineflayer.is_running and not self.server_paused:
            res = self._toggle_pause()
            if res is not None and res.status_code == 200:
                self.server_paused = True
        return self.server_paused

    def unpause(self):
        if self.mineflayer.is_running and self.server_paused:
            res = self._toggle_pause()
            if res is not None and res.status_code == 200:
                self.server_paused = False
            elif res is not None:
                print(res.json())
        return self.server_paused

    def _toggle_pause(self):
        try:
            res = requests.post(f"{self.server}/pause", timeout=60)
        except requests.exceptions.RequestException as e:
            # mineflayer died, the next check_process restarts it
            print(f"Pause request failed: {e}")
            return None
        if res.status_code == 200 and res.json().get("disconnected"):
            # the bot got kicked, wait for mineflayer to exit so check_process restarts it
            print("Bot got disconnected from Minecraft, restarting mineflayer")
            self.mineflayer.wait_for_exit(timeout=10)
        return res
