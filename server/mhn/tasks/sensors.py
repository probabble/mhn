import uuid
from functools import wraps

import sys

from fabric.contrib.files import sed
from fabric.exceptions import NetworkError
from fabric.operations import run, sudo, local, put
from fabric.state import env

from mhn.tasks import celery
from fabric.decorators import task as fabric_task
from fabric.context_managers import settings as fab_settings, cd
from mhn.api.models import Sensor, DeployScript

RFC_2822 = "%a, %d %b %Y %H:%M:%S +0000"

from StringIO import StringIO
from fabric.api import get

from flask import Flask
from mhn import mhn

def health_check(fabric=True):

    def real_decorator(function):

        @wraps(function)
        def _fabric_task(function, fab_env, args, kwargs):
            with fab_settings(**fab_env):
                return fabric_task(function)(*args, **kwargs)

        @wraps(function)
        def wrapper(env, *args, **kwargs):
            try:
                if fabric:
                    result = _fabric_task(function, env, args, kwargs)
                else:
                    result = function(*args, **kwargs)

                if result is True:
                    sensor.status = "OK"
                else:
                    sensor.status = "FAIL"
                    sensor.reason = result
                print "result: {}".format(result)
                print "{name}: {status}".format(**sensor.__dict__)

                hc.save()

            except Exception, e:
                hc.status = "FAIL"
                ex_type, ex, tb = sys.exc_info()
                hc.reason = ex
                hc.exception = traceback.format_exc()

                hc.save()

                print sys.exc_info()
                raise

        return celery.shared_task(wrapper, name=function.__module__ + '.' + function.__name__)

    return real_decorator


def update_sensors():
    for sensor in Sensor.query.all():
        env = dict(
            host=sensor.ip,
            user='b',
            key_filename='/'
        )

@fabric_task
def unconfigured():
    env.hosts = ['honeypie-a253a7da11384347a394cb9a29e241dc']
    env.user = 'pi'
    env.password = 'raspberry'


@fabric_task
def configure_sensors():

    # env.host = 'honeypie'
    # env.user = 'b'
    # env.password = 'bee'

    # start by refreshing the dns.
    # for this to work, we need to allow our users to run this command
    # todo modify the sudoers file on the mhn server machine, adding:
    # `
    # without a password :D
    local("sudo /etc/init.d/nscd restart")

    # first thing we should do is change the hostname of the sensor
    # for now we'll just use a random honeypie hostname.
    # it's pretty unlikely that these will overlap
    try:
        current_hostname = run("hostname")
    except NetworkError, e:
        print "No unconfigured sensors found on the network... nothing to do here."
        return

    if current_hostname == "pi@honeypie-a253a7da11384347a394cb9a29e241dc":
        new_hostname = "honeypie" # + uuid.uuid4().hex
        sudo('hostname {}'.format(new_hostname))
        sudo('echo {} > /etc/hostname'.format(new_hostname))
        sed('/etc/hosts', 'honeypie',new_hostname, use_sudo=True)
    else:
        # we probably shouldn't be here at this point - if the hostname
        # has already been changed, we shouldn't be accessing this sensor
        new_hostname = current_hostname

    sudo('service networking restart')
    # if there isn't a host available at this address,
    # there are no new machines to configure
    # so we can just stop here!
    # except Exception, e:
    #
    #     return

    # find out what honeypots should be installed on this machine
    # one way to do this would be to copy a txt file to the home dir
    # with the names of the honeypots we want to install
    fd = StringIO()
    get('~/honeypots.txt', fd)
    content = fd.getvalue()
    honeypots_to_install = content.split('\n')

    for honeypot in honeypots_to_install:
        print honeypot
        if not honeypot:
            continue
        with mhn.app_context():
            scripts = DeployScript.query.filter_by(name=honeypot).all()

        if len(scripts) > 1:
            raise Exception("found multiple honeypots when searching for {}".format(honeypot))
        elif len(scripts) < 1:
            raise Exception("found no honeypots when searching for {}".format(honeypot))
        else:
            script = scripts[0]

        # run the deploy script on the sensor
        filename = '/tmp/{host}_{honeypot}.sh'.format(host=new_hostname, honeypot=script.id)
        file = open(filename, 'w')
        file.write(script.script)
        file.close()

        put(filename, '/home/pi/deploy.sh', use_sudo=True)
        url = mhn.config.get("SERVER_BASE_URL")
        deploy_key = mhn.config.get("DEPLOY_KEY")
        with cd("/home/pi/"):
            run("bash /tmp/deploy.sh {url} {deploy_key}".format(url=url, deploy_key=deploy_key, id=script.id))

    # this should register the
    # generate new key for this sensor


    # disable password auth
    sed('/etc/ssh/sshd_config',
        '#PasswordAuthentication yes',
        'PasswordAuthentication no',
        use_sudo=True)

    # Deny root login
    sed('/etc/ssh/sshd_config',
        'PermitRootLogin yes',
        'PermitRootLogin no',
        use_sudo=True)
