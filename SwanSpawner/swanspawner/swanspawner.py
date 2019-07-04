# Author: Danilo Piparo, Enric Tejedor, Diogo Castro 2015
# Copyright CERN

"""CERN Specific Spawner class"""

import pickle, struct, re
import calendar, datetime
import os, pwd, subprocess
from tornado import gen
from traitlets import (
    Unicode,
    Bool,
    Int,
    List,
    Dict
)

import contextlib
import random
import psutil
from socket import (
    socket,
    SO_REUSEADDR,
    AF_INET,
    SOCK_STREAM,
    SOL_SOCKET,
    gethostname,
)


def define_SwanSpawner_from(base_class):
    """
        The Spawner need to inherit from a proper upstream Spawner (i.e Docker or Kube).
        But since our personalization, added on top of those, is exactly the same for all,
        by allowing a dynamic inheritance we can re-use the same code on all cases.
        This function returns our SwanSpawner, inheriting from a class (upstream Spawner)
        given as parameter.
    """

    class SwanSpawner(base_class):

        lcg_view_path = Unicode(
            default_value='/cvmfs/sft.cern.ch/lcg/views',
            config=True,
            help='Path where LCG views are stored in CVMFS.'
        )

        lcg_rel_field = Unicode(
            default_value='LCG-rel',
            help='LCG release field of the Spawner form.'
        )

        platform_field = Unicode(
            default_value='platform',
            help='Platform field of the Spawner form.'
        )

        user_script_env_field = Unicode(
            default_value='scriptenv',
            help='User environment script field of the Spawner form.'
        )

        user_n_cores = Unicode(
            default_value='ncores',
            help='User number of cores field of the Spawner form.'
        )

        user_memory = Unicode(
            default_value='memory',
            help='User available memory field of the Spawner form.'
        )

        auth_script = Unicode(
            default_value='',
            config=True,
            help='Script to authenticate.'
        )

        hadoop_auth_script = Unicode(
            config=True,
            help='Script to authenticate with hadoop clusters.'
        )

        init_k8s_user = Unicode(
            config=True,
            help='Script to authenticate with k8s clusters.'
        )

        local_home = Bool(
            default_value=False,
            config=True,
            help="If True, a physical directory on the host will be the home and not eos."
        )

        eos_path_prefix = Unicode(
            default_value='/eos/user',
            config=True,
            help='Path in eos preceeding the /t/theuser directory (e.g. /eos/user, /eos/scratch/user).'
        )
        
        yarn_config_script = Unicode(
            default_value='/cvmfs/sft.cern.ch/lcg/etc/hadoop-confext/hadoop-setconf.sh',
            config=True,
            help='Path in CVMFS of the script to configure a YARN cluster.'
        )

        k8s_config_script = Unicode(
            default_value='/cvmfs/sft.cern.ch/lcg/etc/hadoop-confext/k8s-setconf.sh',
            config=True,
            help='Path in CVMFS of the script to configure a K8s cluster.'
        )

        spark_cluster_field = Unicode(
            default_value='spark-cluster',
            help='Spark cluster name field of the Spawner form.'
        )

        spark_max_sessions = Int(
            default_value=1,
            config=True,
            help='Number of parallel Spark sessions per user session (container).'
        )

        spark_session_num_ports = Int(
            default_value=3,
            config=True,
            help='Number of ports opened per user session (container).'
        )

        spark_session_port_range_start = Int(
            default_value=5001,
            config=True,
            help='Start of the port range that is used by the user session (container).'
        )

        spark_session_port_range_end = Int(
            default_value=5300,
            config=True,
            help='End of the port range that is used by the user session (container).'
        )

        available_cores = List(
            default_value=['1'],
            config=True,
            help='List of cores options available to the user'
        )

        available_memory = List(
            default_value=['8'],
            config=True,
            help='List of memory options available to the user'
        )

        graphite_metric_path = Unicode(
            default_value='c5.swan',
            config=True,
            help='Base path for SWAN in Grafana metrics'
        )

        graphite_server = Unicode(
            default_value='filer-carbon.cern.ch',
            config=True,
            help='Server where to post the metrics collected'
        )

        graphite_server_port_batch = Int(
            default_value=2004,
            config=True,
            help='Port of the server where to post the metrics collected'
        )

        shared_volumes = Dict(
            config=True,
            help='Volumes to be mounted with a "shared" tag. This allows mount propagation.',
        )

        extra_env = Dict(
            config=True,
            help='Extra environment variables to pass to the container',
        )

        image_slc6 = Unicode(
            config=True,
            help='TEMPORARY: SLC6 image to spawn a session'
        )

        metrics_on = Bool(
            default_value=True,
            config=True,
            help="If True, it will send the metrics to CERN grafana (temporary, we will separate the metrics from the spwaner)."
        )


        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.offload = False
            self.this_host = gethostname().split('.')[0]

        def options_from_form(self, formdata):
            options = {}
            options[self.lcg_rel_field]         = formdata[self.lcg_rel_field][0]
            options[self.platform_field]        = formdata[self.platform_field][0]
            options[self.user_script_env_field] = formdata[self.user_script_env_field][0]
            options[self.spark_cluster_field]   = formdata[self.spark_cluster_field][0] if self.spark_cluster_field in formdata.keys() else 'none'
            options[self.user_n_cores]          = int(formdata[self.user_n_cores][0]) if formdata[self.user_n_cores][0] in self.available_cores else int(self.available_cores[0])
            options[self.user_memory]           = formdata[self.user_memory][0] + 'G' if formdata[self.user_memory][0] in self.available_memory else self.available_memory[0] + 'G'

            self.offload = options[self.spark_cluster_field] != 'none'

            return options

        def get_env(self):
            """
            Set base environmental variables
            """
            env = super().get_env()

            username = self.user.name
            userid = pwd.getpwnam(username).pw_uid
            if self.local_home:
                homepath = "/scratch/%s" %(username)
            else:
                homepath = "%s/%s/%s" %(self.eos_path_prefix, username[0], username)

            env.update(dict(
                ROOT_LCG_VIEW_NAME     = self.user_options[self.lcg_rel_field],
                ROOT_LCG_VIEW_PLATFORM = self.user_options[self.platform_field],
                USER_ENV_SCRIPT        = self.user_options[self.user_script_env_field],
                ROOT_LCG_VIEW_PATH     = self.lcg_view_path,
                USER                   = username,
                USER_ID                = str(userid),
                HOME                   = homepath,

                JPY_USER               = self.user.name,
                JPY_COOKIE_NAME        = self.user.server.cookie_name,
                JPY_BASE_URL           = self.user.base_url,
                JPY_HUB_PREFIX         = self.hub.base_url,
                JPY_HUB_API_URL        = self.hub.api_url
            ))

            if self.extra_env:
                env.update(self.extra_env)

            # Only dockerspawner has these vars, and for now is the only one that needs this
            # code since we still don't have spark ready to work with a kubernetes deployment
            if hasattr(self, 'extra_host_config') and hasattr(self, 'extra_create_kwargs'):
                # Clear old state
                self.extra_host_config['port_bindings'] = {}
                self.extra_create_kwargs['ports'] = []

                # Avoid overriding the default container output port, defined by the Spawner
                if not self.use_internal_ip:
                    self.extra_host_config['port_bindings'][self.port] = (self.host_ip,)

                if self.offload:
                    cluster = self.user_options[self.spark_cluster_field]
                    env['SPARK_CLUSTER_NAME'] 		    = cluster
                    env['SPARK_USER'] 		            = username
                    env['SERVER_HOSTNAME']   	 	    = os.uname().nodename
                    env['MAX_MEMORY']         	   	    = self.user_options[self.user_memory]

                    if cluster == 'k8s':
                        env['SPARK_CONFIG_SCRIPT'] = self.k8s_config_script
                    else:
                        env['SPARK_CONFIG_SCRIPT'] = self.yarn_config_script

                    # Asks the OS for random ports to give them to Docker,
                    # so that Spark can be exposed to the outside
                    # Reserves the ports so that other processes don't use them
                    # before Docker opens them
                    spark_ports = []
                    for _ in range(self.spark_session_num_ports * self.spark_max_sessions):
                        try:
                            reserved_port =  self.get_reserved_port(self.spark_session_port_range_start, self.spark_session_port_range_end)
                        except Exception as ex:
                            self.log.error("Error while allocating ports for Spark: %s", ex, exc_info=True)
                            raise RuntimeError("Error while allocating ports for Spark. Please try again.")
                        self.extra_host_config['port_bindings'][reserved_port] = reserved_port
                        self.extra_create_kwargs['ports'].append(reserved_port)
                        spark_ports.append(str(reserved_port))
                    env["SPARK_PORTS"] = ",".join(spark_ports)

            return env

        @gen.coroutine
        def start(self):
            """Start the container and perform the operations necessary for mounting
            EOS, authenticating HDFS and authenticating K8S.
            """

            username = self.user.name
            platform = self.user_options[self.platform_field]
            lcg_rel = self.user_options[self.lcg_rel_field]
            cluster = self.user_options[self.spark_cluster_field]
            cpu_quota = self.user_options[self.user_n_cores]
            mem_limit = self.user_options[self.user_memory]

            try:
                if not self.local_home and self.auth_script:
                    # When using CERNBox as home, obtain credentials for the user
                    subprocess.call(['sudo', self.auth_script, username], timeout=60)
                    self.log.debug("We are in SwanSpawner. Credentials for %s were requested.", username)

                if not os.path.exists(self.lcg_view_path):
                    raise ValueError(
                        """
                        Could not initialize software stack, please <a href="https://cern.ch/ssb" target="_blank">check service status</a> or <a href="https://cern.service-now.com/service-portal/function.do?name=swan" target="_blank">report an issue</a>
                        """
                    )

                if not os.path.exists(self.lcg_view_path + '/' + lcg_rel + '/' + platform):
                    raise ValueError(
                        """
                        Configuration not available: please select other <b>Software stack</b> and <b>Platform</b>.
                        """
                    )

                # If the user selects a Spark Cluster we need to generate a token to allow him in
                if self.offload:
                    # FIXME: temporaly limit Cloud Container to specific platform and software stack
                    if cluster == 'k8s' and "dev" not in lcg_rel:
                        raise ValueError(
                            """
                            Configuration unsupported: 
                            only <b>Software stack: Bleeding Edge Python2/Python3</b> is supported for Cloud Containers
                            """
                        )

                    # If the user selects a Spark Cluster we need to generate some tokens
                    # FIXME: Dont hardcode hadoop path, use hadoop_host_path and hadoop_container_path
                    hadoop_host_path = '/spark/' + username
                    hadoop_container_path = '/spark'

                    # Ensure that env variables are properly cleared
                    self.env.pop('WEBHDFS_TOKEN', None)
                    self.env.pop('HADOOP_TOKEN_FILE_LOCATION', None)
                    self.env.pop('KUBECONFIG', None)

                    # Set authentication and authorization for the user
                    if cluster == 'k8s':
                        subprocess.call([
                            'sudo',
                            self.init_k8s_user,
                            username
                        ], timeout=60)

                        # set location of user kubeconfig for Spark
                        if os.path.exists(hadoop_host_path + '/k8s-user.config'):
                            self.env['KUBECONFIG'] = hadoop_container_path + '/k8s-user.config'
                        else:
                            raise RuntimeError(
                                """
                                Problem connecting to Cloud Containers cluster. 
                                Please <a href="https://cern.service-now.com/service-portal/function.do?name=swan" target="_blank">report an issue</a>
                                """
                            )

                        subprocess.call([
                            'sudo',
                            self.hadoop_auth_script,
                            'analytix',
                            username
                        ], timeout=60)

                        # Set default EOS krb5 cache location to hadoop container path for k8s
                        self.env['KRB5CCNAME'] = hadoop_container_path + '/krb5cc'
                    else:
                        subprocess.call([
                            'sudo',
                            self.hadoop_auth_script,
                            cluster,
                            username
                        ], timeout=60)

                        # Set default location for krb5cc in tmp directory for yarn
                        self.env['KRB5CCNAME'] = '/tmp/krb5cc'

                    # set location of hadoop token file and webhdfs token for Spark
                    if os.path.exists(hadoop_host_path + '/hadoop.toks') and os.path.exists(hadoop_host_path + '/webhdfs.toks'):
                        self.env['HADOOP_TOKEN_FILE_LOCATION'] = hadoop_container_path + '/hadoop.toks'
                        with open(hadoop_host_path + '/webhdfs.toks', 'r') as webhdfs_token_file:
                            self.env['WEBHDFS_TOKEN'] = webhdfs_token_file.read()
                    else:
                        if cluster == 'nxcals':
                            raise ValueError(
                                """
                                Access to the NXCALS cluster is not granted. 
                                Please <a href="https://wikis.cern.ch/display/NXCALS/Data+Access+User+Guide#DataAccessUserGuide-nxcals_access" target="_blank">request access</a>
                                """
                            )
                        elif cluster == 'k8s':
                            # if there is no HADOOP_TOKEN_FILE or WEBHDFS_TOKEN with K8s we ignore (no HDFS access granted)
                            pass
                        else:
                            # yarn clusters require HADOOP_TOKEN_FILE and WEBHDFS_TOKEN containing YARN and HDFS tokens
                            raise ValueError(
                                "Configuration unsupported: "
                                "only <b>Software stack: Bleeding Edge</b> is supported for Cloud Containers"
                            )

                # The bahaviour changes if this if dockerspawner or kubespawner
                if hasattr(self, 'extra_host_config'):
                    # Due to dockerpy limitations in the current version, we cannot use --cpu to limit cpu.
                    # This is an alternative (and old) way of doing it
                    self.extra_host_config.update({
                        'cpu_period' : 100000,
                        'cpu_quota' : 100000 * cpu_quota
                    })
                else:
                    self.cpu_limit = cpu_quota
                self.mem_limit = mem_limit

                # update start success metrics
                self._send_user_metrics()
                self._send_start_exception_metric(None)

                return startup
            except BaseException as e:
                # update start failure metric
                self._send_start_exception_metric(e)
                raise e

        @gen.coroutine
        def stop(self, now=False):
            """ Overwrite default spawner to get status of container """
            stop_result = yield super().stop(now)

            # Return 0 exit code as container got stopped correctly
            container_exit_code = "0"
            self._send_exit_metric(container_exit_code)

            return stop_result

        @gen.coroutine
        def poll(self):
            """ Overwrite default poll to get status of container """
            container_exit_code = yield super().poll()

            # None if single - user process is running.
            # Integer exit code status, if it is not running and not stopped by JupyterHub.
            if container_exit_code is not None:
                self._send_exit_metric(container_exit_code)

            return container_exit_code

        @property
        def volume_mount_points(self):
            """
            Override this method to take into account the "shared" volumes.
            """
            return self.get_volumes(only_mount=True)


        @property
        def volume_binds(self):
            """
            Since EOS now uses autofs (mounts and unmounts mount points automatically), we have to mount
            eos in the container with the propagation option set to "shared".
            This means that: when users try to access /eos/projects, the endpoint will be mounted automatically,
            and made available in the container without the need to restart the session (it gets propagated).
            This also means that, if the user endpoint fails, when it gets back up it will be made available in
            the container without the need to restart the session.
            The Spawnwer/dockerpy do not support this option. But, if volume_bins return a list of string, they will
            pass the list forward until the container construction, without checking or trying to manipulate the list.
            """
            return self.get_volumes()

        def get_volumes(self, only_mount=False):

            def _fmt(v):
                return self.format_volume_name(v, self)

            def _convert_list(volumes, binds, mode="rw"):
                for k, v in volumes.items():
                    m = mode
                    if isinstance(v, dict):
                        if "mode" in v:
                            m = v["mode"]
                        v = v["bind"]

                    if only_mount:
                        binds.append(_fmt(v))
                    else:
                        binds.append("%s:%s:%s" % (_fmt(k), _fmt(v), m))
                return binds

            binds = _convert_list(self.volumes, [])
            binds = _convert_list(self.read_only_volumes, binds, mode="ro")
            return _convert_list(self.shared_volumes, binds, mode="shared")

        @staticmethod
        def get_reserved_port(start, end, n_tries=10):
            """
                Reserve a random available port.
                It puts the door in TIME_WAIT state so that no other process gets it when asking for a random port,
                but allows processes to bind to it, due to the SO_REUSEADDR flag.
                From https://github.com/Yelp/ephemeral-port-reserve
            """
            for i in range(n_tries):
                try:
                    with contextlib.closing(socket()) as s:
                        port = random.randint(start, end)
                        net_connections = psutil.net_connections()
                        # look through the list of active connections to check if the port is being used or not and return FREE if port is unused
                        if next((conn.laddr[1] for conn in net_connections if conn.laddr[1] == port),'FREE') != 'FREE':
                            raise Exception('Port {} is in use'.format(port))
                        s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
                        s.bind(('127.0.0.1', port))

                        # the connect below deadlocks on kernel >= 4.4.0 unless this arg is greater than zero
                        s.listen(1)

                        sockname = s.getsockname()

                        # these three are necessary just to get the port into a TIME_WAIT state
                        with contextlib.closing(socket()) as s2:
                            s2.connect(sockname)
                            s.accept()
                            return sockname[1]
                except:
                    if i == n_tries - 1:
                        raise

        def _send_user_metrics(self):
            """
            Send user chosen options to the metrics server.
            This will allow us to see what users are choosing from within Grafana.
            """

            try:
                if self.metrics_on:
                    base_path = self._get_base_metric_path()
                    base_key = 'spawn_form'
                    date = self._get_date_seconds()

                    metrics = []
                    for (key, value) in self.user_options.items():
                        if key == self.user_script_env_field:
                            path = ".".join([base_path, base_key, key])
                            metrics.append((path, (date, 1 if value else 0)))
                        else:
                            value_cleaned = str(value).replace('/', '_')
                            path = ".".join([base_path, base_key, key, value_cleaned])
                            # Metrics values are a number
                            metrics.append((path, (date, 1)))

                    self._send_graphite_metrics(metrics)
            except Exception as ex:
                self.log.error("Failed to send metrics: %s", ex, exc_info=True)


        def _send_start_exception_metric(self, start_exception):
            """
            Send start status of the container.
            Status should be None in case of successful start, or BaseException exception
            """

            try:
                if self.metrics_on:
                    date = self._get_date_seconds()

                    metric_path = self._get_base_metric_path()
                    key = "spawn_exception"
                    value_cleaned = start_exception.__class__.__name__ if start_exception is not None else "None"

                    metric = [
                        (".".join([metric_path, key, value_cleaned]), (date, 1))
                    ]

                    self.log.info("StartExceptionMetric: %s" % metric)

                    # FIXME: send metrics

            except Exception as ex:
                self.log.error("Failed to send metrics: %s", ex, exc_info=True)

        def _send_exit_metric(self, exit_return_code):
            """
            Send exit status of the container.
            Status should be 0 in case of successful exit, or return code as integer
            """

            try:
                if self.metrics_on:
                    date = self._get_date_seconds()

                    metric_path = self._get_base_metric_path()
                    key = "container_exit_code"

                    if exit_return_code.isdigit():
                        value_cleaned = exit_return_code
                    else:
                        result = re.search('ExitCode=(\d+)', exit_return_code)
                        if not result:
                            raise Exception("unknown exit code format for this Spawner")

                        value_cleaned = result.group(1)

                    metrics = [
                        ".".join([
                            metric_path,
                            key,
                            value_cleaned
                        ]),
                        (date, 1)
                    ]

                    self.log.info("ExitReturnCodeMetric: %s" % metrics)

                    # FIXME: send metrics
            except Exception as ex:
                self.log.error("Failed to send metrics: %s", ex, exc_info=True)

        def _get_date_seconds(self):
            d = datetime.datetime.utcnow()
            return calendar.timegm(d.timetuple())

        def _get_base_metric_path(self):
            return ".".join([self.graphite_metric_path, self.this_host])

        def _send_graphite_metrics(self, metrics):
            # Serialize the message and send everything in on single package
            payload = pickle.dumps(metrics, protocol=2)
            header = struct.pack("!L", len(payload))
            message = header + payload

            # Send the message
            conn = socket(AF_INET, SOCK_STREAM)
            conn.settimeout(2)
            conn.connect((self.graphite_server, self.graphite_server_port_batch))
            conn.send(message)
            conn.close()

    return SwanSpawner