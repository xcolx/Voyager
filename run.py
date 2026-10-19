from voyager import Voyager

voyager = Voyager(
    mc_port=58388,  # port shown after "Open to LAN"
    skill_library_dir="./ckpt_world1",
    resume=True,  # continue from ./ckpt; move ckpt away to start a new world from scratch
    llm_backend="claude_cli",
    action_agent_model_name="sonnet",
    curriculum_agent_model_name="sonnet",
    critic_agent_model_name="haiku",
    curriculum_agent_qa_model_name="haiku",
    skill_manager_model_name="haiku",
)
voyager.learn()