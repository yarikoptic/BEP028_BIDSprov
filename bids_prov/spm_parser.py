import sys
import click
import json
import os
import re
from difflib import SequenceMatcher

from collections import defaultdict

from . import spm_load_config as conf
from . import get_id


def format_activity_name(s, l=30):  # s example : cfg_basicio.file_dir.file_ops.file_move._1
    if s.startswith("spm."):
        s = s[4:]
    tmp = s.split(".")  # ['cfg_basicio', 'file_dir', 'file_ops', 'file_move', '_1']
    while sum(map(len, tmp)) > l:  # sum of the lengths of each element of tmp
        tmp = tmp[1:]
    return ".".join(tmp)  # file_dir.file_ops.file_move._1


def get_input_entity(left, right):
    """get input Entity if possible else return None
    Very few entities in detectable inputs. We find for example the read files.

    left: string
        left side of ' = '
    right: string
        right side of ' = '
    """
    # print(f"left : {left}")
    # print(f"right : {right}")
    if conf.has_parameter(left):  # r"[^\.]+\(\d+\)"
        # a string contains at least one parameter if it does not start with a dot and contains at least one digit
        # between brackets.
        # if there are parameters, they are necessarily in the left part (function call) and this is not an entity
        # print("the string contains parameters so this is not an input_entity")
        return None
    if not next(re.finditer(conf.PATH_REGEX, right), None):
        # r"([A-Za-z]:|[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)((/[A-Za-z0-9_.-]+)+)"
        # if not None (if it doesn't match with conf.PATH_REGEX), enter in if
        # print("the string does not match with conf.PATH_REGEX")
        return None
    if next(re.finditer(conf.FILE_REGEX, right), None) is None:  # r"(\.[a-z]{1,3}){1,2}"
        # the string does not contain a filename extension so this is not an entity
        # print("the string does not contain a filename so this is not an input_entity")
        return None

    entity_label = re.sub(r"[{};\'\"]", "", right).split("/")[-1]  # sub allows you to remove braces; apostrophe and
    # quotation mark.
    # If we have : "$HOME/nidmresults-examples/spm_default/ds011/sub-01/func/sub-01_task-tonecounting_bold.nii.gz",
    # the line will return "sub-01_task-tonecounting_bold.nii.gz" and not "sub-01_task-tonecounting_bold.nii.gz'};"
    # print(f'entity label : {entity_label}')
    entity = {
        "@id": "niiri:" + entity_label + get_id(),
        "label": entity_label,
        "prov:atLocation": right[2:-3],  # similar processing with respect to the entity_label variable. The line
        # removes "{'" at the beginning and "'};" at the end
    }
    # print(f'entity : {entity}')
    return entity


def preproc_param_value(val):
    if val[0] == "[":  # example : [4 2] becomes [4, 2]
        return val.replace(" ", ", ")
    return val


def readlines(filename):
    """Read lines from the original batch.m file

    A definition should be associated with a single line in the output
    """
    cnt = 0
    with open(filename) as fd:
        for line in fd:
            if line.startswith("matlabbatch"):
                _line = line[:-1]  # remove "\n"
                while _line.count("{") != _line.count("}"):
                    _line += next(fd, "} ")[:-1].lstrip() + ","
                while _line.count("[") != _line.count("]"):
                    _line = _line.strip() + " " + next(fd)[:-1].lstrip()
                yield _line


def group_lines(lines):
    """Group line by their activity id

    The activity id is between curly brackets, for every line

    Parameters
    ----------
    lines: iterable[str]
        lines to be grouped, where every element is a python string

    Returns
    -------
    dict[int, str]
        a mapping from activity id to lines belonging to this activity

    Example
    -------
    >>> from bids_prov.spm_parser import group_lines
    >>> lines = ["batch{1}.file_ops.file_move.call", "batch{1}.file_ops.file_move.different.call"]
    >>> group_lines(lines)
    {'file_ops.file_move._1': ['call', 'different.call']}
    """
    res = defaultdict(list)  # keys : activity number, values : rest of the line
    for line in lines:
        a = re.search(r"\{\d+\}", line)
        if a:
            g = a.group()[1:-1]  # retrieves the batch number without the braces
            res[g].append(line[a.end() + 1:])  # retrieves the rest of the line without the dot after the brace of
            # the activity number

    new_res = dict()  # keys : common prefix shared by the functions of an activity, values : rest of each line
    for k, v in res.items():
        common_prefix = os.path.commonprefix([_.split(" = ")[0] for _ in v])
        new_key = f"{common_prefix}_{k}"  # add to the common prefix the activity number
        new_res[new_key] = [_[len(common_prefix):] for _ in v]  # keep the rest of the line
    return new_res


def get_records(task_groups: dict, records=defaultdict(list)):
    """Take the result of `group_lines` and output the corresponding
    JSON-ld graph as a python dict

    See Also
    --------
    bids_prov.spm_parser.group_lines
    """
    entities_ids = set()
    # print(f"task_groups : {task_groups}")
    for activity_name, values in task_groups.items():
        # print('-'*50)
        activity_id = "niiri:" + activity_name + get_id()
        activity = {
            "@id": activity_id,
            "label": format_activity_name(activity_name),
            "used": list(),
            "wasAssociatedWith": "RRID:SCR_007037",  # TODO ?
        }
        # print(f"activity : {activity}, values : {task_groups[activity_name]}")
        input_entities, output_entities = list(), list()
        params = []

        conf_outputs = next(
            (k for k in conf.static["activities"] if k in activity_name), None
        )  # checks if spatial.preproc is contained in the name of the current activity and if so returns
        # spatial.preproc
        if conf_outputs is not None:
            conf_outputs = conf.static["activities"][conf_outputs]
            activity_name = conf_outputs["name"]  # FIXME ? useless ?
            for output in conf_outputs["outputs"]:
                output_entities.append(
                    {
                        "@id": output + get_id(),
                        "label": output,
                        "prov:atLocation": output,
                        "wasGeneratedBy": activity_id,
                    }
                )

        for line in values:
            split = line.split(" = ")  # split in 2 at the level of the equal the rest of the action
            if len(split) != 2:
                # print(f"could not parse {line}")
                continue
            left, right = split

            _in = get_input_entity(left, right)
            if _in:
                input_entities.append(_in)
            elif conf.has_parameter(left) or conf.has_parameter(activity_name):
                # or has_parameter(activity_name) is mandatory because if in our activity we have only one call
                # to a function, the common part will be full and so left will be empty
                dependency = re.search(conf.DEPENDENCY_REGEX, right, re.IGNORECASE)  # cfg_dep\(['"]([^'"]*)['"]\,.*
                # check if the line call cfg_dep and retrieve the first parameter
                dep_number = re.search(r"{(\d+)}", right)  # retrieve all digits between parenthesis
                if dependency is not None:
                    parts = dependency.group(1).split(": ")  # retrieve name of the output_entity
                    # if right = "cfg_dep('Move/Delete Files: Moved/Copied Files', substruct('.',...));"
                    # return : ['Move/Delete Files', 'Moved/Copied Files']
                    closest_activity = next(
                        filter(
                            lambda a: a["label"].endswith(dep_number.group(1)),
                            records["prov:Activity"],
                        ),
                        None,
                    )  # among all the activities, check if one of them has a label ending with "dep_number" and
                    # return the activity
                    # print(f"records : {records}")
                    # print(f"closest_activity : {closest_activity}")
                    if closest_activity is None:
                        continue
                    output_id = "niiri:" + parts[-1].replace(" ", "") + dep_number.group(1)
                    # example : "niiri:oved/CopiedFiles1
                    activity["used"].append(output_id)  # adds to the current activity the fact that it has used the
                    # previous entity
                    output_entities.append(
                        {
                            "@id": output_id,
                            "label": parts[-1],
                            # "prov:atLocation": TODO
                            "wasGeneratedBy": closest_activity["@id"],
                            #
                        }
                    )
                else:
                    Warning(f"Could not parse line {line}")
            else:
                # print('params')
                param_name = ".".join(left.split(".")[-2:])  # split left by "." and keep the two last elements
                param_value = preproc_param_value(right[:-1])  # remove ";" at the end of right

                # HANDLE STRUCTS eg. struct('name', {}, 'onset', {}, 'duration', {})
                if param_value.startswith("struct"):
                    continue  # TODO handle dictionary-like parameters

                try:
                    eval(param_value)
                except:
                    Warning(f"could not set {param_name} to {param_value}")
                    continue
                finally:
                    params.append([param_name, param_value])

        # print(f"input_entities : {input_entities}")
        if input_entities:
            used_entities = [e["@id"] for e in input_entities]
            # print(f'activity["used"] : {activity["used"]}')
            activity["used"] = activity["used"] + used_entities  # we add entities from input_entities
        entities = input_entities + output_entities
        if params:
            activity["attributes"] = params
        records["prov:Activity"].append(activity)
        for e in entities:
            if e["@id"] not in entities_ids:
                records["prov:Entity"].append(e)
            entities_ids.add(e["@id"])

    return records


@click.command()
@click.argument("filenames", nargs=-1)
@click.option("--output-file", "-o", required=True)
@click.option(
    "--context-url",
    "-c",
    default=conf.CONTEXT_URL,
)
def spm_to_bids_prov(filenames, output_file, context_url):
    filename = filenames[0]  # FIXME

    graph = conf.get_empty_graph(context_url=context_url)

    lines = readlines(filename)
    tasks = group_lines(lines)
    records = get_records(tasks)
    graph["records"].update(records)

    with open(output_file, "w") as fd:
        json.dump(graph, fd, indent=2)


if __name__ == "__main__":
    sys.exit(spm_to_bids_prov())
