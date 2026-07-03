import json
import os
from pathlib import Path

import networkx as nx
try:
    from pgmpy.models import DiscreteBayesianNetwork
except ImportError:
    from pgmpy.models import BayesianNetwork as DiscreteBayesianNetwork
from pgmpy.readwrite import BIFReader, BIFWriter


def save_pgmpy_model(model, model_dir_path):
    Path(model_dir_path).mkdir(parents=True, exist_ok=True)
    model_path = os.path.join(model_dir_path, "model.bif")
    node_mapping_path = os.path.join(model_dir_path, "node_mapping.json")
    state_mapping_path = os.path.join(model_dir_path, "state_mapping.json")

    node_mapping, state_mapping = generate_node_mapping(model)
    model_with_mapping = apply_mapping_to_model_for_saving(model, node_mapping, state_mapping)

    save_model(model_with_mapping, model_path)
    save_mapping(node_mapping, node_mapping_path)
    save_mapping(state_mapping, state_mapping_path)


def generate_node_mapping(model):
    node_mapping = {node: f"node_{i}" for i, node in enumerate(sorted(model.nodes()))}

    state_set = set()
    for cpd in model.get_cpds():
        for var_name in cpd.state_names:
            state_set.update(cpd.state_names[var_name])

    state_mapping = {s: f"state_{i}" for i, s in enumerate(sorted(state_set))}
    return node_mapping, state_mapping


def apply_mapping_to_model_for_saving(model, node_mapping, state_mapping):
    relabeled_graph = nx.relabel_nodes(model, node_mapping, copy=True)
    relabeled_model = DiscreteBayesianNetwork(relabeled_graph.edges())
    new_cpds = []

    for cpd in model.get_cpds():
        new_variable = node_mapping.get(cpd.variable, cpd.variable)
        new_evidence = [node_mapping.get(v, v) for v in cpd.variables if v != cpd.variable]
        new_cpd = cpd.copy()
        new_cpd.variable = new_variable
        new_cpd.variables = [new_variable] + new_evidence
        new_cpd.state_names = {
            node_mapping.get(k, k): [state_mapping.get(s, s) for s in v]
            for k, v in cpd.state_names.items()
        }
        new_cpd.name_to_no = {
            node_mapping.get(var, var): {state_mapping.get(k, k): v for k, v in idx.items()}
            for var, idx in cpd.name_to_no.items()
        }
        new_cpd.no_to_name = {
            node_mapping.get(var, var): {k: state_mapping.get(v, v) for k, v in idx.items()}
            for var, idx in cpd.no_to_name.items()
        }
        new_cpds.append(new_cpd)

    for new_cpd in new_cpds:
        relabeled_model.add_cpds(new_cpd)
        if not new_cpd.is_valid_cpd():
            raise ValueError(f"Invalid CPD for {new_cpd.variable}")

    assert relabeled_model.check_model(), "Relabeled model is invalid"
    return relabeled_model


def save_model(model, model_path):
    writer = BIFWriter(model)
    writer.write_bif(model_path)


def save_mapping(mapping, mapping_path):
    with open(mapping_path, "w") as f:
        json.dump(mapping, f, indent=4)


def load_model(model_path):
    reader = BIFReader(model_path)
    return reader.get_model()


def load_mapping(mapping_path):
    with open(mapping_path) as f:
        return json.load(f)


def translate_model(model, node_mapping, state_mapping):
    reverse_node = {v: k for k, v in node_mapping.items()}
    reverse_state = {v: k for k, v in state_mapping.items()}

    relabeled_model = DiscreteBayesianNetwork(
        [(reverse_node[x], reverse_node[y]) for x, y in model.edges()]
    )

    for cpd in model.get_cpds():
        new_variable = reverse_node.get(cpd.variable, cpd.variable)
        new_evidence = [reverse_node.get(v, v) for v in cpd.variables if v != cpd.variable]
        new_cpd = cpd.copy()
        new_cpd.variable = new_variable
        new_cpd.variables = [new_variable] + new_evidence
        new_cpd.state_names = {
            reverse_node.get(var, var): [reverse_state.get(s, s) for s in states]
            for var, states in cpd.state_names.items()
        }
        new_cpd.name_to_no = {
            reverse_node.get(var, var): {reverse_state.get(s, s): idx for s, idx in m.items()}
            for var, m in cpd.name_to_no.items()
        }
        new_cpd.no_to_name = {
            reverse_node.get(var, var): {idx: reverse_state.get(s, s) for idx, s in m.items()}
            for var, m in cpd.no_to_name.items()
        }
        relabeled_model.add_cpds(new_cpd)

    assert relabeled_model.check_model(), "Relabeled model is invalid after translation"
    return relabeled_model


def load_pgmpy_model(model_dir_path):
    model_path = os.path.join(model_dir_path, "model.bif")
    node_mapping_path = os.path.join(model_dir_path, "node_mapping.json")
    state_mapping_path = os.path.join(model_dir_path, "state_mapping.json")

    model = load_model(model_path)
    node_mapping = load_mapping(node_mapping_path)
    state_mapping = load_mapping(state_mapping_path)

    return translate_model(model, node_mapping, state_mapping)
