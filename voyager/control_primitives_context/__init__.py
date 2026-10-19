import os
import voyager.utils as U


def load_control_primitives_context(primitive_names=None):
    package_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if primitive_names is None:
        primitive_names = [
            primitive[:-3]
            for primitive in os.listdir(f"{package_path}/control_primitives_context")
            if primitive.endswith(".js")
        ]
    primitives = [
        U.load_text(f"{package_path}/control_primitives_context/{primitive_name}.js")
        for primitive_name in primitive_names
    ]
    return primitives
