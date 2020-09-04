import unittest
import tempfile
import copy
import json
import os

import pandas as pd

from fmridenoise.interfaces.confounds import Confounds
from tests.interfaces.confounds.utils import (ConfoundsGenerator, 
    confound_filename, pipeline_null)

class TestConfoundsStandardCase(unittest.TestCase):
    seed = 0 
    n_volumes = 100
    n_tcompcor = 10 
    n_acompcor = 100 
    n_aroma = 10
    sub = '00' 
    ses = '0'
    task = 'task0'

    def assertEmptyConfounds(self, file):
        '''Assert that confound file exists and is empty'''
        if (not os.path.exists(file) or 
            os.stat(file).st_size != 1):
            msg = f'confounds file {file} should be empty but have ' + \
                  f'{os.stat(file).st_size} characters'
            raise AssertionError(msg)

    def setUp(self):
        self._generate_confounds()
        # Write confounds to file
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conf_filename_tsv = os.path.join(self.temp_dir.name, 
            confound_filename(sub=self.sub, ses=self.ses, task=self.task, ext='tsv'))
        self.conf_filename_json = os.path.join(self.temp_dir.name, 
            confound_filename(sub=self.sub, ses=self.ses, task=self.task, ext='json'))
        self.cg.confounds.to_csv(self.conf_filename_tsv, sep='\t', index=False)
        self.cg.meta_to_json(self.conf_filename_json)


    def tearDown(self):
        self.temp_dir.cleanup()


    def _generate_confounds(self):
        '''Create fake confounds.'''
        self.cg = ConfoundsGenerator(
            n_volumes=self.n_volumes,
            n_tcompcor=self.n_tcompcor,
            n_acompcor=self.n_acompcor,
            n_aroma=self.n_aroma,
            seed=self.seed
        )


    def _recreate_confounds_node(self, pipeline):
        '''Initialize and return instance of tested Confounds interface.'''
        node = Confounds(
            pipeline=pipeline,
            conf_raw=self.conf_filename_tsv,
            conf_json=self.conf_filename_json,
            subject=self.sub,
            task=self.task,
            session=self.ses,
            output_dir=self.temp_dir.name
        )
        return node


    def test_motion_parameters_filtering(self):
        '''Check if 24 motion parameters are correctly filtered from raw 
        confounds.
        '''
        pipeline = copy.deepcopy(pipeline_null)
        for transform in ['raw', 'derivative1', 'power2', 'derivative1_power2']:
            pipeline['confounds']['motion'][transform] = True

        # Run interface & load confounds
        node = self._recreate_confounds_node(pipeline)
        node.run()
        conf_prep = pd.read_csv(node._results['conf_prep'], sep='\t')

        # Recreate correct column names
        hmp_names = [f'{type_}_{axis}' 
            for type_ in ('trans', 'rot') for axis in ('x', 'y', 'z')]
        conf_names = {f'{hmp_name}{suffix}'
            for suffix in ('', '_derivative1', '_power2', '_derivative1_power2') 
            for hmp_name in hmp_names}

        self.assertEqual(conf_names, set(conf_prep.columns))
        self.assertEqual(conf_prep.shape, (self.n_volumes, 24)) 


    def test_tissue_signals_filtering(self):
        '''Check if physiological signals and global signal are correctly 
        filtered from raw confounds. Physiological signals are white matter, csf
        signal.'''
        pipeline = copy.deepcopy(pipeline_null)
        for tissue in ['white_matter', 'csf', 'global_signal']:
            for transform in ['raw', 'derivative1', 'power2', 'derivative1_power2']:
                pipeline['confounds'][tissue][transform] = True

        node = self._recreate_confounds_node(pipeline)
        node.run()
        conf_prep = pd.read_csv(node._results['conf_prep'], sep='\t')

        phys_names = ['csf', 'global_signal', 'white_matter']
        conf_names = {f'{phys_name}{suffix}'
            for suffix in ('', '_derivative1', '_power2', '_derivative1_power2') 
            for phys_name in phys_names}

        self.assertEqual(conf_names, set(conf_prep.columns))
        self.assertEqual(conf_prep.shape, (self.n_volumes, 12)) 


    def test_selective_filtering(self):
        '''Check if confounds (hmp and physiological signals) are correctly 
        filtered for non-standard options.'''
        pipeline = copy.deepcopy(pipeline_null)
        pipeline['confounds']['motion']['derivative1'] = True
        pipeline['confounds']['white_matter']['power2'] = True
        pipeline['confounds']['global_signal']['derivative1_power2'] = True
        pipeline['confounds']['csf']['raw'] = True

        node = self._recreate_confounds_node(pipeline)
        node.run()
        conf_prep = pd.read_csv(node._results['conf_prep'], sep='\t')

        hmp_names = [f'{type_}_{axis}' 
            for type_ in ('trans', 'rot') for axis in ('x', 'y', 'z')]
        conf_names = {f'{hmp_name}_derivative1' for hmp_name in hmp_names}
        conf_names = conf_names.union({
            'white_matter_power2', 
            'global_signal_derivative1_power2',
            'csf'
        })
        
        self.assertEqual(conf_names, set(conf_prep.columns))
        self.assertEqual(conf_prep.shape, (self.n_volumes, 9)) 
        

    def test_acompcor_filtering(self):
        '''Test filtering out first aCompCor components. Filtering procedure 
        should retain first five wm components and first five csf components. 
        Information from confounds json metadata should be used to determine 
        origin of the signal (mask).'''
        pipeline = copy.deepcopy(pipeline_null)
        pipeline['confounds']['acompcor'] = True

        node = self._recreate_confounds_node(pipeline)
        node.run()
        conf_prep = pd.read_csv(node._results['conf_prep'], sep='\t')

        self.assertEqual(set(self.cg.relevant_acompcors), set(conf_prep.columns))
        self.assertEqual(conf_prep.shape, (self.n_volumes, 10)) 


    def test_null_pipeline(self):
        '''Even if aroma is part of denoising strategy, aroma confounds should 
        not be included in preprocessed confounds. Check if interface produces
        empty file.'''
        pipeline = copy.deepcopy(pipeline_null)
        pipeline['aroma'] = True

        node = self._recreate_confounds_node(pipeline)
        node.run()

        # Ensure that file exists and is (almost) empty
        self.assertEmptyConfounds(node._results['conf_prep'])


    def test_spike_regressors(self):
        '''Check if motion regressors are correctly created for specified range
        of both fd and dvars thresholds.'''
        pipeline = copy.deepcopy(pipeline_null)

        test_thrs = [(0.1, 0.1), (0.1, 1.5), (0.5, 0.1),
                    (0.5, 1.5), (0.5, 3.0), (9.9, 9.9)]

        for fd_th, dvars_th in test_thrs:
            with self.subTest(f'Testing fd_th = {fd_th} and dvars_th = {dvars_th}'):
                # Reset files
                self.tearDown()
                self.setUp()

                pipeline['spikes'] = {'fd_th': fd_th, 'dvars_th': dvars_th}

                node = self._recreate_confounds_node(pipeline)
                node.run()

                outlier_scans = self.cg.get_outlier_scans(fd_th, dvars_th)
                outlier_names = {f'motion_outlier_{i:02}' 
                                for i in range(len(outlier_scans))}

                if not outlier_scans:
                    # No outliers should be detected
                    self.assertEmptyConfounds(node._results['conf_prep'])
                else:
                    conf_prep = pd.read_csv(node._results['conf_prep'], sep='\t')
                    outlier_detected = conf_prep.sum(axis=1) == 1
                    outlier_detected = list(outlier_detected[outlier_detected].index)

                    self.assertEqual(outlier_names, set(conf_prep.columns))
                    self.assertEqual(set(outlier_scans), set(outlier_detected))


    def test_conf_prep_name(self):
        '''Test whether preprocessed confounds table is saved as a file having 
        correct BIDS compliant name.'''
        pipeline = copy.deepcopy(pipeline_null)
        pipeline['name'] = 'testPipeline'

        node = self._recreate_confounds_node(pipeline)
        node.run()  
        conf_prep_name = node._results['conf_prep']

        expected_name = os.path.join(
            self.temp_dir.name,
            f"{self.conf_filename_tsv.replace('_desc-confounds_regressors.tsv', '')}" + \
            f"_pipeline-{pipeline['name']}_desc-confounds.tsv"
        )

        self.assertEqual(expected_name, conf_prep_name)


    def test_conf_summary_name(self):
        '''Ensure confounds summary json has proper name'''
        pipeline = copy.deepcopy(pipeline_null)
        pipeline['name'] = 'testPipeline'

        node = self._recreate_confounds_node(pipeline)
        node.run()  
        conf_summary_name = node._results['conf_summary']

        expected_name = os.path.join(
            self.temp_dir.name,
            f"{self.conf_filename_tsv.replace('_desc-confounds_regressors.tsv', '')}" + \
            f"_pipeline-{pipeline['name']}_desc-confounds_summary.json"
        )

        self.assertEqual(expected_name, conf_summary_name)


    def test_conf_summary(self):
        '''Check if summary dict produce correct values. It is validated on 
        pipeline containing only spike detection since spike-related values are
        majority of values stored in summary json.'''
        pipeline = copy.deepcopy(pipeline_null)

        test_thrs = [(0.1, 0.1), (0.1, 1.5), (0.5, 0.1),
                    (0.5, 1.5), (0.5, 3.0), (9.9, 9.9)]

        for fd_th, dvars_th in test_thrs:
            with self.subTest(f'Testing fd_th = {fd_th} and dvars_th = {dvars_th}'):
                self.tearDown()
                self.setUp()
                pipeline['spikes'] = {'fd_th': fd_th, 'dvars_th': dvars_th}

                node = self._recreate_confounds_node(pipeline)
                node.run()

                with open(node._results['conf_summary']) as f:
                    summary_dict = json.load(f)

                # Expected values
                n_outlier_scans = len(self.cg.get_outlier_scans(fd_th, dvars_th))
                perc_outlier_scans = n_outlier_scans / self.n_volumes * 100
                include = (self.cg.mean_fd <= fd_th and
                           self.cg.max_fd <= 5 and
                           perc_outlier_scans <= 20) 

                self.assertEqual(summary_dict['subject'], self.sub)
                self.assertEqual(summary_dict.get('session', ''), self.ses)
                self.assertEqual(summary_dict['task'], self.task)
                self.assertAlmostEqual(summary_dict['max_fd'], self.cg.max_fd)
                self.assertAlmostEqual(summary_dict['mean_fd'], self.cg.mean_fd)
                self.assertEqual(summary_dict['n_spikes'], n_outlier_scans)
                self.assertAlmostEqual(summary_dict['perc_spikes'], 
                                    perc_outlier_scans)
                self.assertEqual(summary_dict['include'], include)

class TestConfoundsNoAromaNoTCompCor(TestConfoundsStandardCase):        
    seed = 0
    n_volumes = 5
    n_tcompcor = 0
    n_acompcor = 100
    n_aroma = 0
    sub = '01'
    ses = '1'
    task = 'task1'

class TestConfoundsNoSessionLetterInSubject(TestConfoundsStandardCase):
    seed = 2 
    n_volumes = 5 
    n_tcompcor = 10 
    n_acompcor = 100 
    n_aroma = 10 
    sub = 'test02' 
    ses = ''
    task = 'task2'

if __name__ == '__main__':
    unittest.main()
