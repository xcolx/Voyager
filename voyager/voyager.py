import copy
import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Dict

import voyager.utils as U
from .env import VoyagerEnv
from .env.bridge import EnvError

from .agents import ActionAgent
from .agents import CriticAgent
from .agents import CurriculumAgent
from .agents import SkillManager


@contextmanager
def timed(label):
    # print how long each phase takes, to see where the time between actions goes
    start = time.perf_counter()
    try:
        yield
    finally:
        print(f"\033[90m[time] {label}: {time.perf_counter() - start:.1f}s\033[0m")


# TODO: remove event memory
class Voyager:
    def __init__(
        self,
        mc_port: int = None,
        azure_login: Dict[str, str] = None,
        server_port: int = 3000,
        openai_api_key: str = None,
        llm_backend: str = "openai",
        env_wait_ticks: int = 20,
        env_request_timeout: int = 600,
        env_step_timeout: int = None,
        max_llm_failures: int = 5,
        max_iterations: int = 160,
        reset_placed_if_failed: bool = False,
        action_agent_model_name: str = "gpt-4",
        action_agent_temperature: float = 0,
        action_agent_task_max_retries: int = 4,
        action_agent_show_chat_log: bool = True,
        action_agent_show_execution_error: bool = True,
        curriculum_agent_model_name: str = "gpt-4",
        curriculum_agent_temperature: float = 0,
        curriculum_agent_qa_model_name: str = "gpt-3.5-turbo",
        curriculum_agent_qa_temperature: float = 0,
        curriculum_agent_warm_up: Dict[str, int] = None,
        curriculum_agent_core_inventory_items: str = r".*_log|.*_planks|stick|crafting_table|furnace"
        r"|cobblestone|dirt|coal|.*_pickaxe|.*_sword|.*_axe",
        curriculum_agent_mode: str = "auto",
        critic_agent_model_name: str = "gpt-4",
        critic_agent_temperature: float = 0,
        critic_agent_mode: str = "auto",
        skill_manager_model_name: str = "gpt-3.5-turbo",
        skill_manager_temperature: float = 0,
        skill_manager_retrieval_top_k: int = 5,
        openai_api_request_timeout: int = 240,
        ckpt_dir: str = "ckpt",
        skill_library_dir: str = None,
        resume: bool = False,
    ):
        """
        The main class for Voyager.
        Action agent is the iterative prompting mechanism in paper.
        Curriculum agent is the automatic curriculum in paper.
        Critic agent is the self-verification in paper.
        Skill manager is the skill library in paper.
        :param mc_port: minecraft in-game port
        :param azure_login: minecraft login config
        :param server_port: mineflayer port
        :param openai_api_key: openai api key, not needed for the claude_cli backend
        :param llm_backend: "openai" for the OpenAI API, "claude_cli" for the local claude CLI
        (gpt-4 maps to sonnet, gpt-3.5-turbo to haiku, or pass Claude model names directly)
        :param env_wait_ticks: how many ticks at the end each step will wait, if you found some chat log missing,
        you should increase this value
        :param env_request_timeout: how many seconds to wait for each step, if the code execution exceeds this time,
        python side will terminate the connection and need to be resumed
        :param env_step_timeout: how many seconds generated code may run before mineflayer aborts it,
        defaults to env_request_timeout - 60
        :param max_llm_failures: how many consecutive LLM failures to tolerate before giving up
        :param reset_placed_if_failed: whether to reset placed blocks if failed, useful for building task
        :param action_agent_model_name: action agent model name
        :param action_agent_temperature: action agent temperature
        :param action_agent_task_max_retries: how many times to retry if failed
        :param curriculum_agent_model_name: curriculum agent model name
        :param curriculum_agent_temperature: curriculum agent temperature
        :param curriculum_agent_qa_model_name: curriculum agent qa model name
        :param curriculum_agent_qa_temperature: curriculum agent qa temperature
        :param curriculum_agent_warm_up: info will show in curriculum human message
        if completed task larger than the value in dict, available keys are:
        {
            "context": int,
            "biome": int,
            "time": int,
            "other_blocks": int,
            "nearby_entities": int,
            "health": int,
            "hunger": int,
            "position": int,
            "equipment": int,
            "chests": int,
            "optional_inventory_items": int,
        }
        :param curriculum_agent_core_inventory_items: only show these items in inventory before optional_inventory_items
        reached in warm up
        :param curriculum_agent_mode: "auto" for automatic curriculum, "manual" for human curriculum
        :param critic_agent_model_name: critic agent model name
        :param critic_agent_temperature: critic agent temperature
        :param critic_agent_mode: "auto" for automatic critic ,"manual" for human critic
        :param skill_manager_model_name: skill manager model name
        :param skill_manager_temperature: skill manager temperature
        :param skill_manager_retrieval_top_k: how many skills to retrieve for each task
        :param openai_api_request_timeout: how many seconds to wait for openai api
        :param ckpt_dir: checkpoint dir
        :param skill_library_dir: skill library dir
        :param resume: whether to resume from checkpoint
        """
        # init env
        self.env = VoyagerEnv(
            mc_port=mc_port,
            azure_login=azure_login,
            server_port=server_port,
            request_timeout=env_request_timeout,
            step_timeout=env_step_timeout,
        )
        self.env_wait_ticks = env_wait_ticks
        self.reset_placed_if_failed = reset_placed_if_failed
        self.max_iterations = max_iterations
        self.max_llm_failures = max_llm_failures

        # set openai api key
        if openai_api_key:
            os.environ["OPENAI_API_KEY"] = openai_api_key

        # init agents
        self.action_agent = ActionAgent(
            model_name=action_agent_model_name,
            temperature=action_agent_temperature,
            request_timout=openai_api_request_timeout,
            llm_backend=llm_backend,
            ckpt_dir=ckpt_dir,
            resume=resume,
            chat_log=action_agent_show_chat_log,
            execution_error=action_agent_show_execution_error,
        )
        self.action_agent_task_max_retries = action_agent_task_max_retries
        self.curriculum_agent = CurriculumAgent(
            model_name=curriculum_agent_model_name,
            temperature=curriculum_agent_temperature,
            qa_model_name=curriculum_agent_qa_model_name,
            qa_temperature=curriculum_agent_qa_temperature,
            request_timout=openai_api_request_timeout,
            llm_backend=llm_backend,
            ckpt_dir=ckpt_dir,
            resume=resume,
            mode=curriculum_agent_mode,
            warm_up=curriculum_agent_warm_up,
            core_inventory_items=curriculum_agent_core_inventory_items,
        )
        self.critic_agent = CriticAgent(
            model_name=critic_agent_model_name,
            temperature=critic_agent_temperature,
            request_timout=openai_api_request_timeout,
            llm_backend=llm_backend,
            mode=critic_agent_mode,
        )
        self.skill_manager = SkillManager(
            model_name=skill_manager_model_name,
            temperature=skill_manager_temperature,
            retrieval_top_k=skill_manager_retrieval_top_k,
            request_timout=openai_api_request_timeout,
            llm_backend=llm_backend,
            ckpt_dir=skill_library_dir if skill_library_dir else ckpt_dir,
            resume=True if resume or skill_library_dir else False,
        )
        self.recorder = U.EventRecorder(ckpt_dir=ckpt_dir, resume=resume)
        self.resume = resume

        # init variables for rollout
        self.action_agent_rollout_num_iter = -1
        self.task = None
        self.context = ""
        self.messages = None
        self.conversations = []
        self.last_events = None
        # skill descriptions are written in the background while the next task is proposed
        self._skill_pool = ThreadPoolExecutor(max_workers=1)
        self._pending_skill = None

    def reset(self, task, context="", reset_env=True):
        self.action_agent_rollout_num_iter = 0
        self.task = task
        self.context = context
        if self.last_events and self.env.has_reset:
            # start from the last observation, a fresh mineflayer is only needed after an error,
            # and the env already rejoins on its own then
            events = [copy.deepcopy(self.last_events[-1])]
        else:
            if reset_env:
                self.env.reset(
                    options={
                        "mode": "soft",
                        "wait_ticks": self.env_wait_ticks,
                    }
                )
            # step to peek an observation, time of day and difficulty are left to the world
            events = self.env.step("")
        skills = self.skill_manager.retrieve_skills(query=self.context)
        print(
            f"\033[33mRender Action Agent system message with {len(skills)} skills\033[0m"
        )
        system_message = self.action_agent.render_system_message(skills=skills)
        human_message = self.action_agent.render_human_message(
            events=events, code="", task=self.task, context=context, critique=""
        )
        self.messages = [system_message, human_message]
        print(
            f"\033[32m****Action Agent human message****\n{human_message.content}\033[0m"
        )
        assert len(self.messages) == 2
        self.conversations = []
        return self.messages

    def close(self):
        self.env.close()

    def step(self):
        if self.action_agent_rollout_num_iter < 0:
            raise ValueError("Agent must be reset before stepping")
        with timed("action agent"):
            ai_message = self.action_agent.llm(self.messages)
        print(f"\033[34m****Action Agent ai message****\n{ai_message.content}\033[0m")
        self.conversations.append(
            (self.messages[0].content, self.messages[1].content, ai_message.content)
        )
        parsed_result = self.action_agent.process_ai_message(message=ai_message)
        success = False
        if isinstance(parsed_result, dict):
            code = parsed_result["program_code"] + "\n" + parsed_result["exec_code"]
            with timed("code execution"):
                events = self.env.step(
                    code,
                    programs=self.skill_manager.programs,
                )
            self.recorder.record(events, self.task)
            self.curriculum_agent.update_deaths(events)
            self.action_agent.update_chest_memory(events[-1][1]["nearbyChests"])
            with timed("critic"):
                success, critique = self.critic_agent.check_task_success(
                    events=events,
                    task=self.task,
                    context=self.context,
                    chest_observation=self.action_agent.render_chest_observation(),
                    max_retries=5,
                )

            if self.reset_placed_if_failed and not success:
                # revert all the placing event in the last step
                blocks = []
                positions = []
                for event_type, event in events:
                    if event_type == "onSave" and event["onSave"].endswith("_placed"):
                        block = event["onSave"].split("_placed")[0]
                        position = event["status"]["position"]
                        blocks.append(block)
                        positions.append(position)
                new_events = self.env.step(
                    f"await givePlacedItemBack(bot, {U.json_dumps(blocks)}, {U.json_dumps(positions)})",
                    programs=self.skill_manager.programs,
                )
                events[-1][1]["inventory"] = new_events[-1][1]["inventory"]
                events[-1][1]["voxels"] = new_events[-1][1]["voxels"]
            new_skills = self.skill_manager.retrieve_skills(
                query=self.context
                + "\n\n"
                + self.action_agent.summarize_chatlog(events)
            )
            system_message = self.action_agent.render_system_message(skills=new_skills)
            human_message = self.action_agent.render_human_message(
                events=events,
                code=parsed_result["program_code"],
                task=self.task,
                context=self.context,
                critique=critique,
            )
            self.last_events = copy.deepcopy(events)
            self.messages = [system_message, human_message]
        else:
            assert isinstance(parsed_result, str)
            print(f"\033[34m{parsed_result} Trying again!\033[0m")
            self.recorder.record([], self.task)
        assert len(self.messages) == 2
        self.action_agent_rollout_num_iter += 1
        done = (
            self.action_agent_rollout_num_iter >= self.action_agent_task_max_retries
            or success
        )
        info = {
            "task": self.task,
            "success": success,
            "conversations": self.conversations,
        }
        if success:
            assert (
                "program_code" in parsed_result and "program_name" in parsed_result
            ), "program and program_name must be returned when success"
            info["program_code"] = parsed_result["program_code"]
            info["program_name"] = parsed_result["program_name"]
        else:
            print(
                f"\033[32m****Action Agent human message****\n{self.messages[-1].content}\033[0m"
            )
        return self.messages, 0, done, info

    def rollout(self, *, task, context, reset_env=True):
        self.reset(task=task, context=context, reset_env=reset_env)
        while True:
            messages, reward, done, info = self.step()
            if done:
                break
        return messages, reward, done, info

    def _recover_env(self, retries=3):
        # rejoin with a fresh mineflayer, the server keeps inventory, equipment and position
        for i in range(retries):
            try:
                self.last_events = self.env.reset(
                    options={"wait_ticks": self.env_wait_ticks}
                )
                return
            except EnvError as e:
                if i == retries - 1:
                    raise
                print(f"\033[31mRejoining failed ({e}), retrying\033[0m")
                time.sleep(5 * (i + 1))

    def _on_agent_failure(self, e, failures):
        # LLM or parsing errors do not touch the world, back off and try again
        failures += 1
        print(f"\033[41mAgent failed ({failures}/{self.max_llm_failures}): {e}\033[0m")
        traceback.print_exc()
        if failures >= self.max_llm_failures:
            raise RuntimeError(f"Giving up after {failures} agent failures in a row") from e
        time.sleep(30 * failures)
        return failures

    def _wait_for_skill(self):
        # the next rollout retrieves skills and runs their code, so the new one must be saved first
        if self._pending_skill is None:
            return
        try:
            with timed("waiting for skill description"):
                self._pending_skill.result()
        except Exception as e:
            print(f"\033[41mFailed to add the new skill: {e}\033[0m")
            traceback.print_exc()
        self._pending_skill = None

    def learn(self, reset_env=True):
        self._recover_env()
        agent_failures = 0

        while True:
            if self.recorder.iteration > self.max_iterations:
                print("Iteration limit reached")
                break
            try:
                with timed("curriculum"):
                    task, context = self.curriculum_agent.propose_next_task(
                        events=self.last_events,
                        chest_observation=self.action_agent.render_chest_observation(),
                        max_retries=5,
                    )
            except Exception as e:
                agent_failures = self._on_agent_failure(e, agent_failures)
                continue
            print(
                f"\033[35mStarting task {task} for at most {self.action_agent_task_max_retries} times\033[0m"
            )
            self._wait_for_skill()
            try:
                messages, reward, done, info = self.rollout(
                    task=task,
                    context=context,
                    reset_env=reset_env,
                )
            except EnvError as e:
                # use red color background to print the error
                print("Your last round rollout terminated due to error:")
                print(f"\033[41m{e}\033[0m")
                traceback.print_exc()
                info = {
                    "task": task,
                    "success": False,
                }
                self._recover_env()
            except Exception as e:
                # the task is not counted as failed, the bot never got a fair try
                agent_failures = self._on_agent_failure(e, agent_failures)
                continue
            agent_failures = 0

            if info["success"]:
                self._pending_skill = self._skill_pool.submit(
                    self.skill_manager.add_new_skill, info
                )

            self.curriculum_agent.update_exploration_progress(info)
            print(
                f"\033[35mCompleted tasks: {', '.join(self.curriculum_agent.completed_tasks)}\033[0m"
            )
            print(
                f"\033[35mFailed tasks: {', '.join(self.curriculum_agent.failed_tasks)}\033[0m"
            )

        self._wait_for_skill()
        return {
            "completed_tasks": self.curriculum_agent.completed_tasks,
            "failed_tasks": self.curriculum_agent.failed_tasks,
            "skills": self.skill_manager.skills,
        }

    def decompose_task(self, task):
        if not self.last_events:
            self._recover_env()
        return self.curriculum_agent.decompose_task(task, self.last_events)

    def inference(self, task=None, sub_goals=[], reset_mode="soft", reset_env=True):
        """
        Run in a new world to start from an empty inventory, the bot is never wiped in place.
        reset_mode is kept for compatibility, a "hard" reset is no longer supported.
        """
        if not task and not sub_goals:
            raise ValueError("Either task or sub_goals must be provided")
        if not sub_goals:
            sub_goals = self.decompose_task(task)
        self.env.reset(
            options={
                "mode": reset_mode,
                "wait_ticks": self.env_wait_ticks,
            }
        )
        self.curriculum_agent.completed_tasks = []
        self.curriculum_agent.failed_tasks = []
        self.last_events = self.env.step("")
        while self.curriculum_agent.progress < len(sub_goals):
            next_task = sub_goals[self.curriculum_agent.progress]
            context = self.curriculum_agent.get_task_context(next_task)
            print(
                f"\033[35mStarting task {next_task} for at most {self.action_agent_task_max_retries} times\033[0m"
            )
            messages, reward, done, info = self.rollout(
                task=next_task,
                context=context,
                reset_env=reset_env,
            )
            self.curriculum_agent.update_exploration_progress(info)
            print(
                f"\033[35mCompleted tasks: {', '.join(self.curriculum_agent.completed_tasks)}\033[0m"
            )
            print(
                f"\033[35mFailed tasks: {', '.join(self.curriculum_agent.failed_tasks)}\033[0m"
            )
