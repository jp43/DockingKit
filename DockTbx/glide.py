import os
import sys
import glob
import shutil
import subprocess

import numpy as np

from DockTbx import method

from DockTbx.tools import reader
from DockTbx.tools import mol2
from DockTbx.license import check as chkl

required_programs = ['prepwizard', 'glide', 'ligprep', 'glide_sort', 'pdbconvert']

default_settings = {'poses_per_lig': '10', 'pose_rmsd': '0.5', 'precision': 'SP'}

class Glide(method.DockingMethod):

    def __init__(self, name, site, options):

        super(Glide, self).__init__(name, site, options)

        # set box center
        center = site[1] # set box
        self.options['grid_center'] = ', '.join(map(str.strip, center.split(',')))

        # set box size
        boxsize = site[2]
        boxsize = map(str.strip, boxsize.split(','))
        self.options['innerbox'] = ', '.join(map(str,map(int,map(float,boxsize))))

        outerbox = []
        for idx, xyz in enumerate(['x', 'y', 'z']):
            self.options['act'+xyz+'range'] = str(float(boxsize[idx]) + 10.0)
            outerbox.append(self.options['act'+xyz+'range'])

        self.options['outerbox'] = ', '.join(outerbox)

        self.tmpdirline = ""
        if 'tmpdir' in self.options:
            self.tmpdirline = "export SCHRODINGER_TMPDIR=%s"%self.options['tmpdir']

    def write_docking_script(self, filename, file_r, file_l):
        """ Write docking script for glide """
        locals().update(self.options)

        # prepare protein cmd (the protein structure is already assumed to be minimized/protonated with prepwizard)
        prepwizard_cmd = chkl.eval("prepwizard -fix %(file_r)s target.mae"%locals(), 'schrodinger')    

        # prepare grid and docking cmd
        glide_grid_cmd = chkl.eval("glide grid.in", 'schrodinger')
        glide_dock_cmd = chkl.eval("glide dock.in", 'schrodinger')

        tmpdirline = self.tmpdirline
    
        # write glide script
        with open(filename, 'w') as file:
            script ="""#!/bin/bash
%(tmpdirline)s

# (A) Prepare receptor
%(prepwizard_cmd)s

# (B) Prepare grid
echo "USECOMPMAE YES
INNERBOX %(innerbox)s
ACTXRANGE %(actxrange)s
ACTYRANGE %(actyrange)s
ACTZRANGE %(actzrange)s
GRID_CENTER %(grid_center)s
OUTERBOX %(outerbox)s
ENTRYTITLE target
GRIDFILE grid.zip
RECEP_FILE target.mae" > grid.in
%(glide_grid_cmd)s

# (C) convert ligand to maestro format
structconvert -imol2 %(file_l)s -omae lig.mae

# (D) perform docking
echo "WRITEREPT YES
USECOMPMAE YES
DOCKING_METHOD confgen
POSES_PER_LIG %(poses_per_lig)s
POSE_RMSD %(pose_rmsd)s
GRIDFILE $PWD/grid.zip
LIGANDFILE $PWD/lig.mae
PRECISION %(precision)s" > dock.in
%(glide_dock_cmd)s"""% locals()
            file.write(script)
 
    def extract_docking_results(self, file_s, input_file_r, input_file_l):
        """Extract Glide docking results""" 

        if os.path.exists('dock_pv.maegz'):
            # (1) cmd to extract results
            subprocess.check_output('glide_sort -r sort.rept dock_pv.maegz -o dock_sorted.mae', shell=True, executable='/bin/bash')

            # (2) convert to .mol2
            subprocess.check_output('mol2convert -n 2: -imae dock_sorted.mae -omol2 dock_sorted.mol2', shell=True, executable='/bin/bash')

            if os.path.exists('dock_sorted.mol2'):
                ligname = reader.open(input_file_l).ligname
                mol2.update_mol2file('dock_sorted.mol2', 'lig-.mol2', ligname=ligname, multi=True)
                # extract scores
                with open('dock.rept', 'r') as ffin:
                    with open(file_s, 'w') as ffout:
                        line = ffin.next()
                        while not line.startswith('===='):
                            line = ffin.next()
                        while True:
                            line = ffin.next()
                            if line.strip():
                                print >> ffout, line[43:51].strip()
                            else:
                                break

    def get_tmpdir_line(self):
        if self.options['tmpdir']:
            line = "export SCHRODINGER_TMPDIR=%(tmpdir)s"%locals()
        else:
            line = ""

    def write_rescoring_script(self, filename, file_r, files_l):
        """Rescore using Glide SP scoring function"""
        locals().update(self.options)

        files_l_joined = ' '.join(files_l)

        # prepare protein cmd (the protein structure is already assumed to be minimized/protonated with prepwizard)
        prepwizard_cmd = chkl.eval("prepwizard -fix %(file_r)s target.mae"%locals(), 'schrodinger')

        # prepare grid and scoring cmd
        glide_grid_cmd = chkl.eval("glide grid.in", 'schrodinger') # grid prepare
        glide_dock_cmd = chkl.eval("glide dock.in", 'schrodinger') # docking command
        tmpdirline = self.tmpdirline

        with open(filename, 'w') as file:
            script ="""#!/bin/bash
%(tmpdirline)s
cat %(files_l_joined)s > lig.mol2

# (A) Prepare receptor
%(prepwizard_cmd)s

# (B) Prepare grid
echo "USECOMPMAE YES
INNERBOX %(innerbox)s
ACTXRANGE %(actxrange)s
ACTYRANGE %(actyrange)s
ACTZRANGE %(actzrange)s
GRID_CENTER %(grid_center)s
OUTERBOX %(outerbox)s
ENTRYTITLE target
GRIDFILE grid.zip
RECEP_FILE target.mae" > grid.in
%(glide_grid_cmd)s


# (C) convert ligand to maestro format
structconvert -imol2 lig.mol2 -omae lig.mae

# (D) perform rescoring
echo "WRITEREPT YES
USECOMPMAE YES
DOCKING_METHOD inplace
GRIDFILE $PWD/grid.zip
LIGANDFILE $PWD/lig.mae
PRECISION SP" > dock.in

%(glide_dock_cmd)s"""% locals()
            file.write(script)

    def extract_rescoring_results(self, filename): 
        idxs = []
        scores = []

        with open('dock.scor', 'r') as ffin:
            line = ffin.next()
            while not line.startswith('===='):
                line = ffin.next()
            while True:
                line = ffin.next()
                if line.strip():
                    idxs.append(int(line[36:42].strip()))
                    scores.append(line[43:51].strip())
                else:
                    break

        scores = np.array(scores)
        scores = scores[np.argsort(idxs)]
        with open(filename, 'w') as ffout:
            for sc in scores:
                print >> ffout, sc

    def cleanup(self):
        pass
