import sqlalchemy
from sqlalchemy import Table, Column, Integer, String, Boolean, ForeignKey
from bob.db.sqlalchemy_migration import Enum, relationship
from sqlalchemy.orm import backref
from sqlalchemy.ext.declarative import declarative_base

import os
import sys

if sys.version_info[0] >= 3:
  from pickle import dumps, loads
else:
  from cPickle import dumps, loads

from .tools import logger

Base = declarative_base()

Status = ('submitted', 'queued', 'waiting', 'executing', 'success', 'failure')

class ArrayJob(Base):
  """This class defines one element of an array job."""
  __tablename__ = 'ArrayJob'

  unique = Column(Integer, primary_key = True)
  id = Column(Integer)
  job_id = Column(Integer, ForeignKey('Job.unique'))
  status = Column(Enum(*Status))
  result = Column(Integer)

  job = relationship("Job", backref='array', order_by=id)

  def __init__(self, id, job_id):
    self.id = id
    self.job_id = job_id
    self.status = Status[0]
    self.result = None

  def std_out_file(self):
    return self.job.std_out_file() + "." + str(self.id) if self.job.log_dir else None

  def std_err_file(self):
    return self.job.std_err_file() + "." + str(self.id) if self.job.log_dir else None

  def __str__(self):
    n = "<ArrayJob %d> of <Job %d>" % (self.id, self.job.id)
    if self.result is not None: r = "%s (%d)" % (self.status, self.result)
    else: r = "%s" % self.status
    return "%s : %s" % (n, r)

  def format(self, format):
    """Formats the current job into a nicer string to fit into a table."""

    job_id = "%d - %d" % (self.job.id, self.id)
    status = "%s" % self.status + (" (%d)" % self.result if self.result is not None else "" )

    return format.format(job_id, self.job.queue_name, status)


class Job(Base):
  """This class defines one Job that was submitted to the Job Manager."""
  __tablename__ = 'Job'

  unique = Column(Integer, primary_key = True) # The unique ID of the job (not corresponding to the grid ID)
  command_line = Column(String(255))           # The command line to execute, converted to one string
  name = Column(String(20))                    # A hand-chosen name for the task
  queue_name = Column(String(20))              # The name of the queue
  grid_arguments = Column(String(255))         # The kwargs arguments for the job submission (e.g. in the grid)
  id = Column(Integer, unique = True)          # The ID of the job as given from the grid
  log_dir = Column(String(255))                # The directory where the log files will be put to
  array_string = Column(String(255))           # The array string (only needed for re-submission)
  stop_on_failure = Column(Boolean)            # An indicator whether to stop depending jobs when this job finishes with an error

  status = Column(Enum(*Status))
  result = Column(Integer)

  def __init__(self, command_line, name = None, log_dir = None, array_string = None, queue_name = 'local', stop_on_failure = False, **kwargs):
    """Constructs a Job object without an ID (needs to be set later)."""
    self.command_line = dumps(command_line)
    self.name = name
    self.queue_name = queue_name   # will be set during the queue command later
    self.grid_arguments = dumps(kwargs)
    self.log_dir = log_dir
    self.stop_on_failure = stop_on_failure
    self.array_string = dumps(array_string)
    self.submit()


  def submit(self, new_queue = None):
    """Sets the status of this job to 'submitted'."""
    self.status = 'submitted'
    self.result = None
    if new_queue is not None:
      self.queue_name = new_queue
    for array_job in self.array:
      array_job.status = 'submitted'
      array_job.result = None

  def queue(self, new_job_id = None, new_job_name = None, queue_name = None):
    """Sets the status of this job to 'queued' or 'waiting'."""
    # update the job id (i.e., when the job is executed in the grid)
    if new_job_id is not None:
      self.id = new_job_id

    if new_job_name is not None:
      self.name = new_job_name

    if queue_name is not None:
      self.queue_name = queue_name

    new_status = 'queued'
    self.result = None
    # check if we have to wait for another job to finish
    for job in self.get_jobs_we_wait_for():
      if job.status not in ('success', 'failure'):
        new_status = 'waiting'
      elif self.stop_on_failure and job.status == 'failure':
        new_status = 'failure'

    # reset the queued jobs that depend on us to waiting status
    for job in self.get_jobs_waiting_for_us():
      if job.status == 'queued':
        job.status = 'failure' if new_status == 'failure' else 'waiting'

    self.status = new_status
    for array_job in self.array:
      array_job.status = new_status


  def execute(self, array_id = None):
    """Sets the status of this job to 'executing'."""
    self.status = 'executing'
    if array_id is not None:
      for array_job in self.array:
        if array_job.id == array_id:
          array_job.status = 'executing'

    # sometimes, the 'finish' command did not work for array jobs,
    # so check if any old job still has the 'executing' flag set
    for job in self.get_jobs_we_wait_for():
      if job.array and job.status == 'executing':
        job.finish(0, -1)



  def finish(self, result, array_id = None):
    """Sets the status of this job to 'success' or 'failure'."""
    # check if there is any array job still running
    new_status = 'success' if result == 0 else 'failure'
    new_result = result
    finished = True
    if array_id is not None:
      for array_job in self.array:
        if array_job.id == array_id:
          array_job.status = new_status
          array_job.result = result
        if array_job.status not in ('success', 'failure'):
          finished = False
        elif new_result == 0:
          new_result = array_job.result

    if finished:
      # There was no array job, or all array jobs finished
      self.status = 'success' if new_result == 0 else 'failure'
      self.result = new_result

      # update all waiting jobs
      for job in self.get_jobs_waiting_for_us():
        if job.status == 'waiting':
          job.queue()


  def get_command_line(self):
    return loads(str(self.command_line))

  def get_array(self):
    return loads(str(self.array_string))

  def get_arguments(self):
    return loads(str(self.grid_arguments))

  def get_jobs_we_wait_for(self):
    return [j.waited_for_job for j in self.jobs_we_have_to_wait_for if j.waited_for_job is not None]

  def get_jobs_waiting_for_us(self):
    return [j.waiting_job for j in self.jobs_that_wait_for_us if j.waiting_job is not None]


  def std_out_file(self, array_id = None):
    return os.path.join(self.log_dir, (self.name if self.name else 'job') + ".o" + str(self.id)) if self.log_dir else None

  def std_err_file(self, array_id = None):
    return os.path.join(self.log_dir, (self.name if self.name else 'job') + ".e" + str(self.id)) if self.log_dir else None


  def __str__(self):
    id = "%d" % self.id
    if self.array: a = "[%d-%d:%d]" % self.get_array()
    else: a = ""
    if self.name is not None: n = "<Job: %s %s - '%s'>" % (id, a, self.name)
    else: n = "<Job: %s>" % id
    if self.result is not None: r = "%s (%d)" % (self.status, self.result)
    else: r = "%s" % self.status
    return "%s : %s -- %s" % (n, r, " ".join(self.get_command_line()))

  def format(self, format, dependencies = 0, limit_command_line = None):
    """Formats the current job into a nicer string to fit into a table."""
    command_line = " ".join(self.get_command_line())
    if limit_command_line is not None and len(command_line) > limit_command_line:
      command_line = command_line[:limit_command_line-3] + '...'

    job_id = "%d" % self.id + (" [%d-%d:%d]" % self.get_array() if self.array else "")
    status = "%s" % self.status + (" (%d)" % self.result if self.result is not None else "" )

    if dependencies:
      deps = str([dep.id for dep in self.get_jobs_we_wait_for()])
      if dependencies < len(deps):
        deps = deps[:dependencies-3] + '...'
      return format.format(job_id, self.queue_name, status, self.name, deps, command_line)
    else:
      return format.format(job_id, self.queue_name, status, self.name, command_line)



class JobDependence(Base):
  """This table defines a many-to-many relationship between Jobs."""
  __tablename__ = 'JobDependence'
  id = Column(Integer, primary_key=True)
  waiting_job_id = Column(Integer, ForeignKey('Job.unique')) # The ID of the waiting job
  waited_for_job_id = Column(Integer, ForeignKey('Job.unique')) # The ID of the job to wait for

  # This is twisted: The 'jobs_we_have_to_wait_for' field in the Job class needs to be joined with the waiting job id, so that jobs_we_have_to_wait_for.waiting_job is correct
  # Honestly, I am lost but it seems to work...
  waiting_job = relationship('Job', backref = 'jobs_we_have_to_wait_for', primaryjoin=(Job.unique == waiting_job_id), order_by=id) # The job that is waited for
  waited_for_job = relationship('Job', backref = 'jobs_that_wait_for_us', primaryjoin=(Job.unique == waited_for_job_id), order_by=id) # The job that waits

  def __init__(self, waiting_job_id, waited_for_job_id):
    self.waiting_job_id = waiting_job_id
    self.waited_for_job_id = waited_for_job_id



def add_job(session, command_line, name = 'job', dependencies = [], array = None, log_dir = None, stop_on_failure = False, **kwargs):
  """Helper function to create a job, add the dependencies and the array jobs."""
  job = Job(command_line=command_line, name=name, log_dir=log_dir, array_string=array, stop_on_failure=stop_on_failure, kwargs=kwargs)

  session.add(job)
  session.flush()
  session.refresh(job)

  # by default id and unique id are identical, but the id might be overwritten later on
  job.id = job.unique

  for d in dependencies:
    depending = list(session.query(Job).filter(Job.id == d))
    if len(depending):
      session.add(JobDependence(job.unique, depending[0].unique))
    else:
      logger.warn("Could not find dependent job with id %d in database" % d)

  if array:
    (start, stop, step) = array
    # add array jobs
    for i in range(start, stop+1, step):
      session.add(ArrayJob(i, job.unique))

  session.commit()

  return job
