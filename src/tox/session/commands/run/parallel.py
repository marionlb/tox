import os
import subprocess
import sys
from collections import OrderedDict
from threading import Event, Semaphore, Thread

from tox import reporter
from tox.config.parallel import ENV_VAR_KEY as PARALLEL_ENV_VAR_KEY
from tox.util.spinner import Spinner


def run_parallel(config, venv_dict):
    """here we'll just start parallel sub-processes"""
    live_out = config.option.parallel_live
    args = [sys.executable, "-m", "tox"] + config.args
    try:
        position = args.index("--")
    except ValueError:
        position = len(args)
    try:
        parallel_at = args[0:position].index("--parallel")
        del args[parallel_at]
        position -= 1
    except ValueError:
        pass

    max_parallel = config.option.parallel
    if max_parallel is None:
        max_parallel = len(venv_dict)
    semaphore = Semaphore(max_parallel)
    finished = Event()
    sink = None if live_out else subprocess.PIPE

    show_progress = not live_out and reporter.verbosity() > reporter.Verbosity.QUIET
    with Spinner(enabled=show_progress) as spinner:

        def run_in_thread(tox_env, os_env):
            res = None
            env_name = tox_env.envconfig.envname
            try:
                os_env[str(PARALLEL_ENV_VAR_KEY)] = str(env_name)
                args_sub = list(args)
                if hasattr(tox_env, "package"):
                    args_sub.insert(position, str(tox_env.package))
                    args_sub.insert(position, "--installpkg")
                process = subprocess.Popen(
                    args_sub,
                    env=os_env,
                    stdout=sink,
                    stderr=sink,
                    stdin=None,
                    universal_newlines=True,
                )
                res = process.wait()
            finally:
                semaphore.release()
                finished.set()
                tox_env.status = (
                    "skipped tests"
                    if config.option.notest
                    else ("parallel child exit code {}".format(res) if res else res)
                )
                done.add(env_name)
                outcome = spinner.succeed
                if config.option.notest:
                    outcome = spinner.skip
                elif res:
                    outcome = spinner.fail
                outcome(env_name)

            if not live_out:
                out, err = process.communicate()
                if res or tox_env.envconfig.parallel_show_output:
                    outcome = (
                        "Failed {} under process {}, stdout:\n".format(env_name, process.pid)
                        if res
                        else ""
                    )
                    message = "{}{}{}".format(
                        outcome, out, "\nstderr:\n{}".format(err) if err else ""
                    ).rstrip()
                    reporter.quiet(message)

        threads = []
        todo_keys = set(venv_dict.keys())
        todo = OrderedDict((n, todo_keys & set(v.envconfig.depends)) for n, v in venv_dict.items())
        done = set()
        while todo:
            for name, depends in list(todo.items()):
                if depends - done:
                    # skip if has unfinished dependencies
                    continue
                del todo[name]
                venv = venv_dict[name]
                semaphore.acquire(blocking=True)
                spinner.add(name)
                thread = Thread(target=run_in_thread, args=(venv, os.environ.copy()))
                thread.start()
                threads.append(thread)
            if todo:
                # wait until someone finishes and retry queuing jobs
                finished.wait()
                finished.clear()

        for thread in threads:
            thread.join()
