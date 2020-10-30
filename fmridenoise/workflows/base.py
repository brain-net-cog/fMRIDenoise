from nipype import Node, IdentityInterface, Workflow, JoinNode
from fmridenoise.interfaces.smoothing import Smooth
from fmridenoise.interfaces.bids import BIDSGrab, BIDSDataSink, BIDSValidate
from fmridenoise.interfaces.confounds import Confounds, GroupConfounds
from fmridenoise.interfaces.denoising import Denoise
from fmridenoise.interfaces.connectivity import Connectivity, GroupConnectivity
from fmridenoise.interfaces.pipeline_selector import PipelineSelector
from fmridenoise.interfaces.quality_measures import QualityMeasures, PipelinesQualityMeasures
from fmridenoise.interfaces.report_creator import ReportCreator
import fmridenoise.utils.temps as temps
from fmridenoise.utils.utils import create_identity_join_node, create_flatten_identity_join_node
from fmridenoise.parcellation import get_distance_matrix_file_path
from fmridenoise.pipelines import get_pipelines_paths
import logging
import os
import typing as t

logger = logging.getLogger("runtime")
handler = logging.FileHandler("./runtime.log")
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)


class WorkflowBuilder:

    def __init__(self,
                 bids_dir: str,
                 subjects: t.List[str],
                 tasks: t.List[str],
                 conf_raw: t.List[str],
                 conf_json: t.List[str],
                 tr_dic: dict,
                 pipelines_paths: t.List[str],
                 high_pass: float,
                 low_pass: float):
        self.fmri_prep_aroma_files = []
        self.fmri_prep_files = []
        # 1) --- Itersources for all further processing
        # Inputs: fulfilled
        self.pipelineselector = Node(
            PipelineSelector(),
            name="PipelineSelector")
        self.pipelineselector.iterables = ('pipeline_path', pipelines_paths)
        # Outputs: pipeline, pipeline_name, low_pass, high_pass

        # Inputs: fulfilled
        self.subjectselector = Node(
            IdentityInterface(
                fields=['subject']),
            name="SubjectSelector")
        self.subjectselector.iterables = ('subject', subjects)
        # Outputs: subject

        # Inputs: fulfilled
        self.taskselector = Node(
            IdentityInterface(
                fields=['task']),
            name="TaskSelector")
        self.taskselector.iterables = ('task', tasks)
        # Outputs: task

        # 2) --- Loading BIDS files

        # Inputs: subject, session, task
        self.bidsgrabber = Node(
            BIDSGrab(
                conf_raw_files=conf_raw,
                conf_json_files=conf_json),
            name="BidsGrabber")
        # Outputs: fmri_prep, fmri_prep_aroma, conf_raw, conf_json

        # 3) --- Confounds preprocessing

        # Inputs: pipeline, conf_raw, conf_json
        self.prep_conf = Node(
            Confounds(
                output_dir=temps.mkdtemp('prep_conf')
            ), name="ConfPrep")
        # Outputs: conf_prep, conf_summary

        # 4) --- Denoising
        # Inputs: fmri_prep, fmri_prep_aroma, conf_prep, pipeline, entity, tr_dict
        self.denoise = Node(
            Denoise(
                high_pass=high_pass,
                low_pass=low_pass,
                tr_dict=tr_dic,
                output_dir=temps.mkdtemp('denoise')),
            name="Denoiser",
            mem_gb=8)
        # Outputs: fmri_denoised

        # 5) --- Connectivity estimation

        # Inputs: fmri_denoised
        self.connectivity = Node(
            Connectivity(
                output_dir=temps.mkdtemp('connectivity')
            ),
            name='ConnCalc')
        # Outputs: conn_mat, carpet_plot

        # 6) --- Group confounds

        # Inputs: conf_summary, pipeline_name
        # FIXME BEGIN
        # This is part of temporary solution.
        # Group nodes write to bids dir insted of tmp and let files be grabbed by datasink
        os.makedirs(os.path.join(bids_dir, 'derivatives', 'fmridenoise'), exist_ok=True)
        # FIXME END
        self.group_conf_summary = JoinNode(
            GroupConfounds(
                output_dir=os.path.join(bids_dir, 'derivatives', 'fmridenoise'),
            ),
            joinfield=["conf_summary_json_files"],
            joinsource=self.subjectselector,
            name="GroupConf")

        # Outputs: group_conf_summary

        # 7) --- Group connectivity

        # Inputs: corr_mat, pipeline_name

        self.group_connectivity = JoinNode(
            GroupConnectivity(
                output_dir=os.path.join(bids_dir, 'derivatives', 'fmridenoise'),
            ),
            joinfield=["corr_mat"],
            joinsource=self.subjectselector,
            name="GroupConn")

        # Outputs: group_corr_mat

        # 8) --- Quality measures

        # Inputs: group_corr_mat, group_conf_summary, pipeline_name

        self.quality_measures = Node(
            QualityMeasures(
                output_dir=os.path.join(bids_dir, 'derivatives', 'fmridenoise'),
                distance_matrix=get_distance_matrix_file_path()
            ),
            name="QualityMeasures")
        # Outputs: fc_fd_summary, edges_weight, edges_weight_clean
        self.quality_measures_join = create_identity_join_node(
            name='JoinQualityMeasuresOverPipeline',
            joinsource=self.pipelineselector,
            fields=[
                'corr_matrix_plot',
                'corr_matrix_no_high_motion_plot']
        )
        # 10) --- Quality measures across pipelines

        # Inputs: fc_fd_summary, edges_weight
        self.pipelines_join = JoinNode(
            IdentityInterface(fields=['pipelines']),
            name='JoinPipelines',
            joinsource=self.pipelineselector,
            joinfield=['pipelines']
        )
        self.pipelines_quality_measures = JoinNode(
            PipelinesQualityMeasures(
                output_dir=os.path.join(bids_dir, 'derivatives', 'fmridenoise'),
                # TODO: Replace with datasinks for needed output
            ),
            joinsource=self.pipelineselector,
            joinfield=['fc_fd_summary', 'edges_weight', 'edges_weight_clean',
                       'fc_fd_corr_values', 'fc_fd_corr_values_clean'],
            name="PipelinesQualityMeasures")
        self.pipeline_quality_measures_join_tasks = create_flatten_identity_join_node(
            name="JoinPipelinesQualityMeasuresOverTasks",
            joinsource=self.taskselector,
            fields=[
                'plot_pipelines_edges_density',
                'plot_pipelines_edges_density_no_high_motion',
                'plot_pipelines_fc_fd_pearson',
                'plot_pipelines_fc_fd_pearson_no_high_motion',
                'plot_pipelines_fc_fd_uncorr',
                'plot_pipelines_distance_dependence',
                'plot_pipelines_distance_dependence_no_high_motion',
                'plot_pipelines_tdof_loss',
                'corr_matrix_plot',
                'corr_matrix_no_high_motion_plot'],
            flatten_fields=[
                'corr_matrix_plot',
                'corr_matrix_no_high_motion_plot'
            ]
        )
        # Outputs: pipelines_fc_fd_summary, pipelines_edges_weight
        # 11) --- Report from data
        report_dir = os.path.join(bids_dir, 'derivatives', 'fmridenoise', 'report')
        os.makedirs(report_dir, exist_ok=True)
        self.report_creator = Node(
            ReportCreator(
                output_dir=report_dir
            ),
            name='ReportCreator')
        self.report_creator.inputs.tasks = tasks
        # 12) --- Save derivatives
        base_entities = {'bids_dir': bids_dir, 'derivative': 'fmridenoise'}
        self.ds_confounds = Node(BIDSDataSink(),
                                 name="ds_confounds")
        self.ds_confounds.inputs.base_entities = base_entities
        self.ds_denoise = Node(BIDSDataSink(),
                               name="ds_denoise")
        self.ds_denoise.inputs.base_entities = base_entities
        self.ds_connectivity_corr_mat = Node(BIDSDataSink(),
                                             name="ds_connectivity")
        self.ds_connectivity_corr_mat.inputs.base_entities = base_entities
        self.ds_connectivity_carpet_plot = Node(BIDSDataSink(),
                                                name="ds_carpet_plot")
        self.ds_connectivity_carpet_plot.inputs.base_entities = base_entities
        self.ds_connectivity_matrix_plot = Node(BIDSDataSink(),
                                                name="ds_matrix_plot")
        self.ds_connectivity_matrix_plot.inputs.base_entities = base_entities
        self.connections = [
            # bidsgrabber
            (self.subjectselector, self.bidsgrabber, [('subject', 'subject')]),
            (self.taskselector, self.bidsgrabber, [('task', 'task')]),
            # prep_conf
            (self.pipelineselector, self.prep_conf, [('pipeline', 'pipeline')]),
            (self.bidsgrabber, self.prep_conf, [('conf_raw', 'conf_raw'),
                                      ('conf_json', 'conf_json')]),
            # denoise
            (self.prep_conf, self.denoise, [('conf_prep', 'conf_prep')]),
            (self.pipelineselector, self.denoise, [('pipeline', 'pipeline')]),
            # group conf summary
            (self.prep_conf, self.group_conf_summary, [('conf_summary', 'conf_summary_json_files')]),
            # connectivity
            (self.denoise, self.connectivity, [('fmri_denoised', 'fmri_denoised')]),
            # group connectivity
            (self.connectivity, self.group_connectivity, [("corr_mat", "corr_mat")]),
            # quality measures
            (self.pipelineselector, self.quality_measures, [('pipeline', 'pipeline')]),
            (self.group_connectivity, self.quality_measures, [('group_corr_mat', 'group_corr_mat')]),
            (self.group_conf_summary, self.quality_measures, [('group_conf_summary', 'group_conf_summary')]),
            # quality measure join over pipelines
            (self.quality_measures, self.quality_measures_join,
             [('corr_matrix_plot', 'corr_matrix_plot'),
              ('corr_matrix_no_high_motion_plot', 'corr_matrix_no_high_motion_plot')]),
            # pipeline quality measures
            (self.quality_measures, self.pipelines_quality_measures, [
                ('fc_fd_summary', 'fc_fd_summary'),
                ('edges_weight', 'edges_weight'),
                ('edges_weight_clean', 'edges_weight_clean'),
                ('fc_fd_corr_values', 'fc_fd_corr_values'),
                ('fc_fd_corr_values_clean', 'fc_fd_corr_values_clean')]),
            (self.taskselector, self.pipelines_quality_measures, [('task', 'task')]),
            # pipelines_join
            (self.pipelineselector, self.pipelines_join, [('pipeline', 'pipelines')]),
            # pipeline_quality_measures_join
            (self.pipelines_quality_measures, self.pipeline_quality_measures_join_tasks, [
                ('pipelines_fc_fd_summary', 'pipelines_fc_fd_summary'),
                ('plot_pipelines_edges_density', 'plot_pipelines_edges_density'),
                ('plot_pipelines_edges_density_no_high_motion', 'plot_pipelines_edges_density_no_high_motion'),
                ('plot_pipelines_fc_fd_pearson', 'plot_pipelines_fc_fd_pearson'),
                ('plot_pipelines_fc_fd_pearson_no_high_motion', 'plot_pipelines_fc_fd_pearson_no_high_motion'),
                ('plot_pipelines_fc_fd_uncorr', 'plot_pipelines_fc_fd_uncorr'),
                ('plot_pipelines_distance_dependence', 'plot_pipelines_distance_dependence'),
                ('plot_pipelines_distance_dependence_no_high_motion', 'plot_pipelines_distance_dependence_no_high_motion'),
                ('plot_pipelines_tdof_loss', 'plot_pipelines_tdof_loss')
               ]),
            (self.quality_measures_join, self.pipeline_quality_measures_join_tasks,
             [('corr_matrix_plot', 'corr_matrix_plot'),
              ('corr_matrix_no_high_motion_plot', 'corr_matrix_no_high_motion_plot')]),
            # report creator
            (self.pipelines_join, self.report_creator, [('pipelines', 'pipelines')]),
            # all datasinks
            ## ds_denoise
            (self.denoise, self.ds_denoise, [("fmri_denoised", "in_file")]),
            ## ds_connectivity
            (self.connectivity, self.ds_connectivity_corr_mat, [("corr_mat", "in_file")]),
            (self.connectivity, self.ds_connectivity_matrix_plot, [("matrix_plot", "in_file")]),
            (self.connectivity, self.ds_connectivity_carpet_plot, [("carpet_plot", "in_file")]),
            ## ds_confounds
            (self.prep_conf, self.ds_confounds, [("conf_prep", "in_file")]),
        ]
        self.last_join = self.pipeline_quality_measures_join_tasks

    def use_fmri_prep_aroma(self, fmri_aroma_files: t.List[str]):
        self.bidsgrabber.inputs.fmri_prep_aroma_files = fmri_aroma_files
        self.connections += [
            (self.bidsgrabber, self.denoise, [('fmri_prep_aroma', 'fmri_prep_aroma')])]

    def use_fmri_prep(self, fmri_prep_files: t.List[str]):
        self.smooth_signal = Node(
            Smooth(
                output_directory=temps.mkdtemp('smoothing'),
                is_file_mandatory=False),
            name="Smoother")
        self.connections += [
            (self.bidsgrabber, self.smooth_signal, [('fmri_prep', 'fmri_prep')]),
            (self.smooth_signal, self.denoise, [('fmri_smoothed', 'fmri_prep')])]
        self.bidsgrabber.inputs.fmri_prep_files = fmri_prep_files

    def with_sessions(self, sessions: t.List[str]):
        self.sessionselector = Node(
            IdentityInterface(
                fields=['session']),
            name="SessionSelector")
        self.sessionselector.iterables = ('session', sessions)
        self.report_creator.inputs.sessions = sessions
        fields = self.last_join.interface._fields
        self.pipeline_quality_measures_join_sessions = create_flatten_identity_join_node(
            name="JoinPipelinesQualityMeasuresOverSessions",
            fields=fields,
            joinsource=self.sessionselector,
            flatten_fields=fields
        )
        self.connections += [
            (self.sessionselector, self.bidsgrabber, [('session', 'session')]),
            (self.sessionselector, self.pipelines_quality_measures, [('session', 'session')]),
            (self.last_join, self.pipeline_quality_measures_join_sessions, list(zip(fields, fields)))
        ]
        self.last_join = self.pipeline_quality_measures_join_sessions

    def with_runs(self, runs: t.List[str]):
        self.runselector = Node(
            IdentityInterface(
                fields=['run']
            ),
            name="RunSelector")
        self.runselector.iterables = ('run', runs)
        self.report_creator.inputs.runs = runs
        fields = self.last_join.interface._fields
        self.pipeline_quality_measures_join_runs = create_flatten_identity_join_node(
            name="JoinPipelinesQualityMeasuresOverRuns",
            fields=fields,
            joinsource=self.runselector,
            flatten_fields=fields
        )
        self.connections += [
            (self.runselector, self.bidsgrabber, [('run', 'run')]),
            (self.runselector, self.pipelines_quality_measures, [('run', 'run')]),
            (self.last_join, self.pipeline_quality_measures_join_runs, list(zip(fields, fields)))
        ]
        self.last_join = self.pipeline_quality_measures_join_runs

    def build(self, name: str, base_dir: str) -> Workflow:
        wf = Workflow(name=name, base_dir=base_dir)
        self.connections.append(
            (self.last_join, self.report_creator,
             [('plot_pipelines_edges_density', 'plots_all_pipelines_edges_density'),
              ('plot_pipelines_edges_density_no_high_motion', 'plots_all_pipelines_edges_density_no_high_motion'),
              ('plot_pipelines_fc_fd_pearson', 'plots_all_pipelines_fc_fd_pearson_info'),
              ('plot_pipelines_fc_fd_pearson_no_high_motion', 'plots_all_pipelines_fc_fd_pearson_info_no_high_motion'),
              ('plot_pipelines_distance_dependence', 'plots_all_pipelines_distance_dependence'),
              ('plot_pipelines_distance_dependence_no_high_motion',
              'plots_all_pipelines_distance_dependence_no_high_motion'),
              ('plot_pipelines_tdof_loss', 'plots_all_pipelines_tdof_loss'),
              ('corr_matrix_plot', 'plots_pipeline_fc_fd_pearson_matrix'),
              ('corr_matrix_no_high_motion_plot', 'plots_pipeline_fc_fd_pearson_matrix_no_high_motion')]))
        wf.connect(self.connections)
        return wf


def init_fmridenoise_wf(bids_dir,
                        derivatives='fmriprep',
                        task=tuple(),
                        session=tuple(),
                        subject=tuple(),
                        runs=tuple(),
                        pipelines_paths=get_pipelines_paths(),
                        high_pass=0.008,
                        low_pass=0.08,
                        base_dir='/tmp/fmridenoise', 
                        name='fmridenoise_wf'):
    pipelines_paths = list(pipelines_paths)
    bids_validate = Node(BIDSValidate(bids_dir=bids_dir,
                                      derivatives=derivatives,
                                      tasks=task,
                                      sessions=session,
                                      subjects=subject,
                                      runs=runs,
                                      pipelines=pipelines_paths),
                         name='BidsValidate')
    result = bids_validate.run()
    builder = WorkflowBuilder(bids_dir=bids_dir,
                              subjects=result.outputs.subjects,
                              tasks=result.outputs.tasks,
                              conf_raw=result.outputs.conf_raw,
                              conf_json=result.outputs.conf_json,
                              tr_dic=result.outputs.tr_dict,
                              pipelines_paths=pipelines_paths,
                              high_pass=high_pass,
                              low_pass=low_pass)
    if result.outputs.fmri_prep:
        builder.use_fmri_prep(result.outputs.fmri_prep)
    if result.outputs.fmri_prep_aroma:
        builder.use_fmri_prep_aroma(result.outputs.fmri_prep_aroma)
    if result.outputs.sessions:
        builder.with_sessions(result.outputs.sessions)
    if result.outputs.runs:
        builder.with_runs(result.outputs.runs)
    return builder.build(name, base_dir)
