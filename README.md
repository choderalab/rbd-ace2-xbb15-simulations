# rbd-ace2-xbb15-simulations

Code for running MD simulations for XBB.1.5 RBD:ACE2

## License
* This software is licensed under the [MIT license](https://opensource.org/licenses/MIT) - a copy of this license is provided as `SOFTWARE_LICENSE`
* The data in this repository is made available under the Creative Commons [CC0 (“No Rights Reserved”) License](https://creativecommons.org/share-your-work/public-domain/cc0/) - a copy of this license is provided as `DATA_LICENSE`

## Manifest

* `01_input_files` - Contains AMBER input files for MD simulations.
* `02_run_md` - Contains the python script used to run MD in OpenMM
* `03_data` - Contains the trajectories (PDB files for each frame) and bash scripts for running MD

## Contributors

* Ivy Zhang

## Conda environment
The environment file is `rbd-ace2.txt`. It can be used to create a new environment by:
```
mamba create -n new-env
mamba install --name rbd-ace2 --file rbd-ace2.txt
```
where new-env is an environment name of your choice.
