#!/usr/bin/env python
# vim: set fileencoding=utf-8 :
# Andre Anjos <andre.anjos@idiap.ch>
# Wed 24 Aug 2011 16:13:31 CEST

"""A logging Idiap/SGE job manager
"""

__epilog__ = """ For a list of available commands:
  >>> %(prog)s --help

  For a list of options for a particular command:
  >>> %(prog)s <command> --help
"""

import os
import sys
import anydbm
from cPickle import dumps

import argparse

from .. import local, sge
from ..tools import make_shell, random_logdir, logger

def setup(args):
  """Returns the JobManager and sets up the basic infrastructure"""

  kwargs = {'wrapper_script' : args.wrapper_script}
  if args.db: kwargs['database'] = args.db
  if args.local:
    jm = local.JobManagerLocal(**kwargs)
  else:
    jm = sge.JobManagerSGE(**kwargs)

  # set-up logging
  import logging
  if args.debug:
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.DEBUG)
  else:
    logger.setLevel(logging.WARNING)


  return jm

def get_array(array):
  if array is None:
    return None
  start = array.find('-')
  if start == -1:
    a = 1
    b = int(array)
    c = 1
  else:
    a = int(array[0:start])
    step = array.find(':')
    if step == -1:
      b = int(array[start+1:])
      c = 1
    else:
      b = int(array[start+1:step])
      c = int(array[step+1])

  return (a,b,c)


def submit(args):
  """Submission command"""

  # set full path to command
  if not os.path.isabs(args.job[0]):
    args.job[0] = os.path.abspath(args.job[0])

  # automatically set interpreter if required
  if args.python or os.path.splitext(args.job[0])[1] in ('.py',):
    args.job = make_shell(sys.executable, args.job)


  jm = setup(args)
  kwargs = {
      'queue': args.qname,
      'cwd': True,
      'name': args.name,
      'env': args.env,
      'memfree': args.memory,
      'hvmem': args.memory,
      'io_big': args.io_big,
      }

  if args.array is not None:         kwargs['array'] = get_array(args.array)
  if args.log_dir is not None:       kwargs['log_dir'] = args.log_dir
  if args.dependencies is not None:  kwargs['dependencies'] = args.dependencies

  if args.dry_run:
    print '-> Job', args.job, 'to', args.qname, 'with',
    print 'queue:', args.qname,
    print 'memory:', args.memory,
    print 'array:', args.array,
    print 'deps:', args.deps,
    print 'env:', args.env,
    print 'io_big:', args.io_big
    return

  # submit the job
  job_id = jm.submit(args.job, **kwargs)


def resubmit(args):
  """Re-submits the jobs with the given ids."""
  jm = setup(args)
  jm.resubmit(args.job_ids, args.failed_only, args.running_jobs)


def execute(args):
  """Executes the collected jobs on the local machine."""
  if not args.local:
    raise ValueError("The execute command can only be used with the '--local' command line option")
  jm = setup(args)
  jm.run(parallel_jobs=args.parallel, job_ids=args.job_ids)


def list(args):
  """Lists the jobs in the given database."""
  jm = setup(args)
  jm.list(args.job_ids, args.print_array_jobs, args.print_dependencies)


def report(args):
  """Reports the results of the finished (and unfinished) jobs."""
  jm = setup(args)
  jm.report(job_ids=args.job_ids, array_ids=args.array_ids, unfinished=args.unfinished_also, output=not args.errors_only, error=not args.output_only)


def stop(args):
  """Stops (qdel's) the jobs with the given ids."""
  if args.local:
    raise ValueError("Stopping commands locally is not supported (please kill them yourself)")
  jm = setup(args)
  jm.stop_jobs(args.job_ids)


def delete(args):
  """Deletes the jobs from the job manager. If the jobs are still running in the grid, they are stopped."""
  jm = setup(args)
  # first, stop the jobs if they are running in the grid
  if not args.local:
    stop(args)
  # then, delete them from the database
  jm.delete(job_ids=args.job_ids, array_ids=args.array_ids, delete_logs=not args.keep_logs, delete_log_dir=not args.keep_log_dir)


def run_job(args):
  """Starts the wrapper script to execute a job, interpreting the JOB_ID and SGE_TASK_ID keywords that are set by the grid or by us."""
  jm = setup(args)
  job_id = int(os.environ['JOB_ID'])
  array_id = int(os.environ['SGE_TASK_ID']) if os.environ['SGE_TASK_ID'] != 'undefined' else None
  jm.run_job(job_id, array_id)


class AliasedSubParsersAction(argparse._SubParsersAction):
  """Hack taken from https://gist.github.com/471779 to allow aliases in
  argparse for python 2.x (this has been implemented on python 3.2)
  """

  class _AliasedPseudoAction(argparse.Action):
    def __init__(self, name, aliases, help):
      dest = name
      if aliases:
        dest += ' (%s)' % ','.join(aliases)
      sup = super(AliasedSubParsersAction._AliasedPseudoAction, self)
      sup.__init__(option_strings=[], dest=dest, help=help)

  def add_parser(self, name, **kwargs):
    if 'aliases' in kwargs:
      aliases = kwargs['aliases']
      del kwargs['aliases']
    else:
      aliases = []

    parser = super(AliasedSubParsersAction, self).add_parser(name, **kwargs)

    # Make the aliases work.
    for alias in aliases:
      self._name_parser_map[alias] = parser
    # Make the help text reflect them, first removing old help entry.
    if 'help' in kwargs:
      help = kwargs.pop('help')
      self._choices_actions.pop()
      pseudo_action = self._AliasedPseudoAction(name, aliases, help)
      self._choices_actions.append(pseudo_action)

    return parser


def main(command_line_options = None):

  from ..config import __version__

  parser = argparse.ArgumentParser(description=__doc__, epilog=__epilog__,
      formatter_class=argparse.RawDescriptionHelpFormatter)
  # part of the hack to support aliases in subparsers
  parser.register('action', 'parsers', AliasedSubParsersAction)

  # general options
  parser.add_argument('-v', '--verbose', dest='verbose', default=False,
      action='store_true', help='increase verbosity for this script')
  parser.add_argument('-g', '--debug', dest='debug', default=False,
      action='store_true', help='prints out lots of debugging information')
  parser.add_argument('-V', '--version', action='version',
      version='GridTk version %s' % __version__)

  parser.add_argument('-l', '--local', action='store_true',
        help = 'Uses the local job manager instead of the SGE one.')
  cmdparser = parser.add_subparsers(title='commands', help='commands accepted by %(prog)s')

  # subcommand 'submit'
  submit_parser = cmdparser.add_parser('submit', aliases=['sub'],
      help='submits self-contained jobs to the SGE queue and logs them in a private database')
  submit_parser.add_argument('-d', '--db', '--database', metavar='DATABASE', help='replace the default database to be used by one provided by you; this option is only required if you are running outside the directory where you originally submitted the jobs from or if you have altered manually the location of the JobManager database')
  submit_parser.add_argument('-q', '--queue', metavar='QNAME',
      dest='qname', default='all.q', help='the name of the SGE queue to submit the job to (defaults to "%(default)s")')
  submit_parser.add_argument('-m', '--memory', dest='memory', help='Sets both the h_vmem **and** the mem_free parameters when submitting the job to the specified value (e.g. 8G to set the memory requirements to 8 gigabytes)')
  submit_parser.add_argument('-n', '--name', dest='name', help='Sets the jobname')
  submit_parser.add_argument('-x', '--dependencies', type=int,
      default=[], metavar='ID', nargs='*', help='set job dependencies by giving this option an a list of job identifiers separated by spaces')
  submit_parser.add_argument('-l', '--log-dir', metavar='DIR', help='Sets the log directory. By default, "logs" is selected. If the jobs are executed locally, by default the result is written to console.')
  submit_parser.add_argument('-s', '--environment', '--env', metavar='KEY=VALUE',
      dest='env', nargs='*', default=[],
      help='Passes specific environment variables to the job')
  submit_parser.add_argument('-t', '--array', '--parametric', metavar='[start:]stop[-step]',
      dest='array', help='Creates a parametric (array) job. You must specify the stop value, but start (default=1) and step (default=1) can be specified as well.')
  submit_parser.add_argument('-p', '--py', '--python', dest='python', default=False,
      action='store_true', help='Wrap execution of your command using the current python interpreter')
  submit_parser.add_argument('-z', '--dry-run',
      action='store_true', help='Do not really submit anything, just print out what would submit in this case')
  submit_parser.add_argument('-I', '--io-big', dest='io_big', default=False,
      action='store_true', help='Sets "io_big" on the submitted jobs so it limits the machines in which the job is submitted to those that can do high-throughput')
  submit_parser.add_argument('job', metavar='command', nargs=argparse.REMAINDER)
  submit_parser.set_defaults(func=submit)

  # re-submit parser
  resubmit_parser = cmdparser.add_parser('resubmit', aliases=['re'],
      help='Re-submits a list of jobs')
  resubmit_parser.add_argument('-d', '--db', '--database', metavar='DATABASE', help='replace the default database to be used by one provided by you; this option is only required if you are running outside the directory where you originally submitted the jobs from or if you have altered manually the location of the JobManager database')
  resubmit_parser.add_argument('-j', '--job-ids', metavar='ID', nargs='*', type=int, help='List only the jobs with the given ids (by default, all jobs are listed)')
  resubmit_parser.add_argument('-f', '--failed-only', action='store_true', help='Re-submit only jobs that have failed')
  resubmit_parser.add_argument('-a', '--running-jobs', action='store_true', help='Re-submit even jobs that are running or waiting')
  resubmit_parser.set_defaults(func=resubmit)

  # stop parser
  stop_parser = cmdparser.add_parser('stop', help='Stops the execution of jobs in the grid')
  stop_parser.add_argument('-d', '--db', '--database', metavar='DATABASE', help='replace the default database to be used by one provided by you; this option is only required if you are running outside the directory where you originally submitted the jobs from or if you have altered manually the location of the JobManager database')
  stop_parser.add_argument('-j', '--job-ids', metavar='ID', nargs='*', type=int, help='Stop only the jobs with the given ids (by default, all jobs are stopped)')
  stop_parser.set_defaults(func=stop)

  # subcommand 'list'
  list_parser = cmdparser.add_parser('list', aliases=['ls'],
      help='lists jobs stored in the database')
  list_parser.add_argument('-d', '--db', metavar='DATABASE', help='replace the default database by one provided by you; this option is only required if you are running outside the directory where you originally submitted the jobs from or if you have altered manually the location of the JobManager database', nargs='?')
  list_parser.add_argument('-j', '--job-ids', metavar='ID', nargs='*', type=int, help='List only the jobs with the given ids (by default, all jobs are listed)')
  list_parser.add_argument('-a', '--print-array-jobs', action='store_true', help='Report only the jobs with the given array ids. If specified, a single job-id must be given as well.')
  list_parser.add_argument('-x', '--print-dependencies', action='store_true', help='Print the dependencies of the jobs as well.')
  list_parser.set_defaults(func=list)

  report_parser = cmdparser.add_parser('report', aliases=['ref', 'r'],
      help='Iterates through the result and error log files and prints out the logs')
  report_parser.add_argument('-d', '--db', metavar='DATABASE', help='replace the default database to be reported by one provided by you', nargs='?')
  report_parser.add_argument('-e', '--errors-only', action='store_true', help='Only report the error logs (by default, both logs are reported).')
  report_parser.add_argument('-o', '--output-only', action='store_true', help='Only report the output logs  (by default, both logs are reported).')
  report_parser.add_argument('-u', '--unfinished-also', action='store_true', help='Report also the unfinished jobs.')
  report_parser.add_argument('-j', '--job-ids', metavar='ID', nargs='*', type=int, help='Report only the jobs with the given ids (by default, all finished jobs are reported)')
  report_parser.add_argument('-a', '--array-ids', metavar='ID', nargs='*', type=int, help='Report only the jobs with the given array ids. If specified, a single job-id must be given as well.')
  report_parser.set_defaults(func=report)

  # subcommand 'delete'
  delete_parser = cmdparser.add_parser('delete', aliases=['del', 'rm', 'remove'],
      help='removes jobs from the database; if jobs are running or are still scheduled in SGE, the jobs are also removed from the SGE queue')
  delete_parser.add_argument('-d', '--db', metavar='DATABASE', help='replace the default database to be reported by one provided by you', nargs='?')
  delete_parser.add_argument('-j', '--job-ids', metavar='ID', nargs='*', type=int, help='Delete only the jobs with the given ids (by default, all jobs are deleted)')
  delete_parser.add_argument('-a', '--array-ids', metavar='ID', nargs='*', type=int, help='Delete only the jobs with the given array ids. If specified, a single job-id must be given as well.')
  delete_parser.add_argument('-r', '--keep-logs', action='store_true', help='If set, the log files will NOT be removed.')
  delete_parser.add_argument('-R', '--keep-log-dir', action='store_true', help='When removing the logs, keep the log directory.')
  delete_parser.set_defaults(func=delete)

  # subcommand 'execute'
  execute_parser = cmdparser.add_parser('execute', aliases=['exe', 'x'],
      help='Executes the registered jobs on the local machine; only valid in combination with the \'--local\' option.')
  execute_parser.add_argument('-d', '--db', metavar='DATABASE', help='replace the default database to be executed by one provided by you', nargs='?')
  execute_parser.add_argument('-p', '--parallel', type=int, default=1, help='Select the number of parallel jobs that you want to execute locally')
  execute_parser.add_argument('-j', '--job-ids', metavar='ID', nargs='*', type=int, help='Execute only the jobs with the given ids (by default, all unfinished jobs are executed)')
  execute_parser.set_defaults(func=execute)


  # subcommand 'run.job'; this is not seen on the command line since it is the actual wrapper script
  run_parser = cmdparser.add_parser('run-job', help=argparse.SUPPRESS)
  run_parser.add_argument('db', metavar='DATABASE', nargs='?', help=argparse.SUPPRESS)
  run_parser.set_defaults(func=run_job)


  if command_line_options:
    args = parser.parse_args(command_line_options[1:])
    args.wrapper_script = command_line_options[0]
  else:
    args = parser.parse_args()
    args.wrapper_script = sys.argv[0]

  args.func(args)

  return 0
