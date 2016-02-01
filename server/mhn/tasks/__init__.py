from celery import Celery

from mhn import mhn


celery = Celery(include=['mhn.tasks.rules', 'mhn.tasks.sensors'])
celery.conf.update(mhn.config)
TaskBase = celery.Task


class ContextTask(TaskBase):

    abstract = True

    def __call__(self, *args, **kwargs):
        with mhn.app_context():
            return TaskBase.__call__(self, *args, **kwargs)

    def on_failure(self, *args, **kwargs):
        print args, kwargs, "FAIL"

celery.Task = ContextTask
