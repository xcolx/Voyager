import os
import voyager.utils as U


def load_prompt(prompt):
    package_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return U.load_text(f"{package_path}/prompts/{prompt}.txt")
