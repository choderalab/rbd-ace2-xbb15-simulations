#!/usr/bin/env bash
# Set walltime limit
#BSUB -W 168:00 
#
# Set output file
#BSUB -o run_equil_complex.out
#
# Set error file
#BSUB -eo run_equil_complex.stderr
#
# Specify node group
#BSUB -m "lj-gpu ll-gpu ln-gpu"
#BSUB -q gpuqueue
#
# nodes: number of nodes and GPU request
#BSUB -n 1 -R "rusage[mem=10]" -sp 25
#BSUB -gpu "num=1:j_exclusive=yes:mode=shared"
#
# job name (default = name of script file)
#BSUB -J "equil.1.com"

outdir=/data/chodera/zhangi/vir/v29/data/1/
phase=complex

source ~/.bashrc
conda activate rbd-ace2

python /data/chodera/zhangi/vir/v29/run_md/run_equilibration_long.py $outdir $phase --gentle
