import os
import sys
import copy
import math
import time
import pickle
import argparse
import logging
from rich.progress import track

import openmm
from openmm import unit, app

import numpy as np
import mdtraj as md

# Set up logger
_logger = logging.getLogger()
_logger.setLevel(logging.INFO)

# Read args
parser = argparse.ArgumentParser(description='run equilibration')
parser.add_argument('outdir', type=str, help='output directory')
parser.add_argument('phase', type=str, help='phase')
parser.add_argument('--gentle', action='store_true', help='whether to run gentle equilibration')
parser.add_argument('--virtual_bond_chain', type=int, help='index of the chain that should be used in conjunction with the 0th chain to add a virtual bond')
args = parser.parse_args()

def run_gentle_equilibration(topology, positions, system, stages, platform_name='CUDA'):
    """
    Run gentle equilibration.
    
    Parameters
    ----------
    topology : openmm.app.Topology
        topology
    positions : np.array in unit.nanometer
        positions
    system : openmm.System
        system
    stages : list of dicts
        each dict corresponds to a stage of equilibration and contains the equilibration parameters for that stage
        
        equilibration parameters:
            EOM : str
                'minimize' or 'MD' or 'MD_interpolate' (the last one will allow interpolation between 'temperature' and 'temperature_end')
            n_steps : int
                number of steps of MD 
            temperature : openmm.unit.kelvin
                temperature (kelvin)
            temperature_end : openmm.unit.kelvin, optional
                the temperature (kelvin) at which to finish interpolation, if 'EOM' is 'MD_interpolate'
            ensemble : str or None
                'NPT' or 'NVT'
            restraint_selection : str or None
                to be used by mdtraj to select atoms for which to apply restraints
            force_constant : openmm.unit.kilocalories_per_mole/openmm.unit.angstrom**2
                force constant (kcal/molA^2)
            collision_rate : 1/openmm.unit.picoseconds
                collision rate (1/picoseconds)
            timestep : openmm.unit.femtoseconds
                timestep (femtoseconds)
    platform_name : str, default 'CUDA'
        name of platform to be used by OpenMM. If not specified, OpenMM will select the fastest available platform 
        
    """
    for i, parameters in enumerate(stages):
        
        initial_time = time.time()
        print(f"Executing stage {i + 1}", file=sys.stderr)
        
        # Make a copy of the system
        system_copy = copy.deepcopy(system)
        
        # Add restraint
        if parameters['restraint_selection'] is not None:
            print(f"restraint selection: {parameters['restraint_selection']}", file=sys.stderr)
            traj = md.Trajectory(positions, md.Topology.from_openmm(topology))
            selection_indices = traj.topology.select(parameters['restraint_selection'])

            custom_cv_force = openmm.CustomCVForce('(K_RMSD/2)*(RMSD)^2')
            custom_cv_force.addGlobalParameter('K_RMSD', parameters['force_constant'] * 2)
            rmsd_force = openmm.RMSDForce(positions, selection_indices)
            custom_cv_force.addCollectiveVariable('RMSD', rmsd_force)
            system_copy.addForce(custom_cv_force)

        # Set barostat update interval to 0 (for NVT)
        if parameters['ensemble'] == 'NVT':
            force_dict = {force.__class__.__name__: index for index, force in enumerate(system_copy.getForces())}
            system_copy.removeForce(force_dict['MonteCarloBarostat']) # TODO : change this to `system_copy.getForce(force_dict['MonteCarloBarostat']).setFrequency(0)` once the next release comes out (this recently merged PR allows frequency to be 0: https://github.com/openmm/openmm/pull/3411) 
    
        elif parameters['ensemble'] == 'NPT' or parameters['ensemble'] is None:
            pass
        
        else:
            raise Exception("Invalid parameter supplied for 'ensemble'")
            
        # Set up integrator
        temperature = parameters['temperature']
        collision_rate = parameters['collision_rate']
        timestep = parameters['timestep']
        
        if parameters['EOM'] == 'MD_interpolate':
            temperature_end = parameters['temperature_end']
        
        integrator = openmm.LangevinMiddleIntegrator(temperature, collision_rate, timestep)
    
        # Set up context
        platform = openmm.Platform.getPlatformByName(platform_name)
        if platform_name in ['CUDA', 'OpenCL']:
            platform.setPropertyDefaultValue('Precision', 'mixed')
        if platform_name in ['CUDA']:
            platform.setPropertyDefaultValue('DeterministicForces', 'true')
        
        sim = app.Simulation(topology, system_copy, integrator, platform)
        sim.context.setPeriodicBoxVectors(*system_copy.getDefaultPeriodicBoxVectors())
        sim.context.setPositions(positions)
        sim.context.setVelocitiesToTemperature(temperature)
    
        # Create dcd reporter
        report_freq = 1000000 if parameters['restraint_selection'] is None else 1000 # save every 1 ns if there is no restraint, otherwise 1 ps
        report_freq_corrected = int(report_freq / timestep.value_in_unit(unit.femtoseconds)) # correct based on timestep
        dcd_reporter = app.DCDReporter(
            file=os.path.join(args.outdir, f"{args.phase}_traj_{i+1}.dcd"),
            reportInterval=report_freq_corrected
            )
        sim.reporters.append(dcd_reporter)

        # Report before minimization
        state = sim.context.getState(getPositions=True)
        dcd_reporter.report(sim, state)

        # Run minimization or MD
        n_steps = parameters['n_steps']
        n_steps_per_iteration = 100
        
        if parameters['EOM'] == 'minimize':
            sim.minimizeEnergy(maxIterations=n_steps)
            
            # Report after minimization
            state = sim.context.getState(getPositions=True)
            dcd_reporter.report(sim, state)

        elif parameters['EOM'] == 'MD':
            if timestep != 4*unit.femtoseconds:
                for _ in track(range(int(n_steps/n_steps_per_iteration))):
                    sim.step(n_steps_per_iteration)
            else:
                n_steps_per_iteration = 250000
                for iteration in track(range(math.ceil(n_steps/n_steps_per_iteration))):
                    sim.step(n_steps_per_iteration)
                    print(f"{iteration+1} ns completed", file=sys.stderr)
                    with open(os.path.join(args.outdir, f"{args.phase}_state.xml"), "w") as f:
                        state = sim.context.getState(getPositions=True, getVelocities=True)
                        f.write(openmm.XmlSerializer.serialize(state))
              
        elif parameters['EOM'] == 'MD_interpolate':
            temperature_unit = unit.kelvin
            temperatures = np.linspace(temperature/temperature_unit, temperature_end/temperature_unit, int(n_steps/n_steps_per_iteration)) * temperature_unit
            for temperature in track(temperatures):
                sim.integrator.setTemperature(temperature)
                sim.step(n_steps_per_iteration)
    
        else:
            raise Exception("Invalid parameter supplied for 'EOM'")
            
        # Retrieve positions after this stage of equil
        positions = sim.context.getState(getPositions=True).getPositions(asNumpy=True)

        # Update default box vectors for next iteration
        box_vectors = sim.context.getState().getPeriodicBoxVectors()
        system.setDefaultPeriodicBoxVectors(*box_vectors)

        del sim, integrator, system_copy
        
        elapsed_time = time.time() - initial_time
        print(f"\tStage {i + 1} took {elapsed_time} seconds", file=sys.stderr)

    # Update box vectors in topology
    box_vectors_formatted = []
    for vec in box_vectors:
        box_vectors_formatted.append(openmm.Vec3(*[val.value_in_unit_system(unit.md_unit_system) for val in vec]))
    box_vectors_final = unit.Quantity(value=box_vectors_formatted, unit=unit.nanometer)
    topology.setPeriodicBoxVectors(box_vectors_final)
        
    # Save the final equilibrated positions
    openmm.app.PDBxFile.writeFile(topology, positions, open(os.path.join(args.outdir, f"{args.phase}_equilibrated.cif"), "w"), keepIds=True)

    # Save the box vectors
    with open(os.path.join(args.outdir, f'{args.phase}_equilibrated_box_vectors.npy'), 'wb') as f:
        np.save(f, box_vectors)

# Load topology and positions
print("Loading topology and positions", file=sys.stderr)
prmtop = app.amberprmtopfile.AmberPrmtopFile("/data/chodera/zhangi/vir/v29/parameterize-using-amber/reordered.001.ACE2_XBB15_0fA-3.hmass.parm7")
inpcrd = app.amberinpcrdfile.AmberInpcrdFile("/data/chodera/zhangi/vir/v29/parameterize-using-amber/001.ACE2_XBB15_0fA-3_reordered.inpcrd")
topology = prmtop.topology
positions = inpcrd.positions

# Format positions for mdtraj (should be a unitless array of arrays)
formatted_positions = np.zeros(shape=(topology.getNumAtoms(), 3))
for i, pos in enumerate(positions):
    formatted_positions[i] = np.array(pos.value_in_unit(unit.nanometers))

# Load system
print("Loading system", file=sys.stderr)
with open(f"/data/chodera/zhangi/vir/v29/parameterize-using-amber/{args.phase}_solvated.xml", "r") as f:
    system = openmm.XmlSerializer.deserialize(f.read())

# Add virtual bond
if args.phase == 'complex':
    print(f"Adding virtual bond", file=sys.stderr)
    residues = list(md.Topology.from_openmm(topology).residues)
    atom_A = list(residues[0].atoms)[0] # First atom of ACE cap in ACE2 chain
    atom_B = list(residues[646].atoms)[0] # First atom of ACE cap in RBD chain
    force = openmm.CustomBondForce('0')
    force.addBond(atom_A.index, atom_B.index, [])
    system.addForce(force)

if args.gentle:
    # Residues to exclude from restraints: ACE, NME caps + missing loops in cryo-em structure (8FXB) 
    to_exclude = " and not (resn ACE or resn NME) and not (resSeq >= 117 and resSeq <= 123) and not (resSeq >= 681 and resSeq <= 686) and not (resSeq >= 693 and resSeq <= 705)"
    print("Running gentle equil", file=sys.stderr)
    stages = [
        {'EOM': 'minimize', 'n_steps': 10000, 'temperature': 300*unit.kelvin, 'ensemble': None, 'restraint_selection': 'protein and not type H' + to_exclude, 'force_constant': 100*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 1*unit.femtoseconds},
        {'EOM': 'MD_interpolate', 'n_steps': 100000, 'temperature': 100*unit.kelvin, 'temperature_end': 300*unit.kelvin, 'ensemble': 'NVT', 'restraint_selection': 'protein and not type H' + to_exclude, 'force_constant': 100*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 10/unit.picoseconds, 'timestep': 1*unit.femtoseconds},
        {'EOM': 'MD', 'n_steps': 100000, 'temperature': 300, 'ensemble': 'NPT', 'restraint_selection': 'protein and not type H' + to_exclude, 'force_constant': 100*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 10/unit.picoseconds, 'timestep': 1*unit.femtoseconds},
        {'EOM': 'MD', 'n_steps': 250000, 'temperature': 300*unit.kelvin, 'ensemble': 'NPT', 'restraint_selection': 'protein and not type H' + to_exclude, 'force_constant': 10*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 1*unit.femtoseconds},
        {'EOM': 'minimize', 'n_steps': 10000, 'temperature': 300*unit.kelvin, 'ensemble': None, 'restraint_selection': 'protein and backbone' + to_exclude, 'force_constant': 10*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 1*unit.femtoseconds},
        {'EOM': 'MD', 'n_steps': 100000, 'temperature': 300*unit.kelvin, 'ensemble': 'NPT', 'restraint_selection': 'protein and backbone' + to_exclude, 'force_constant': 10*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 1*unit.femtoseconds},
        {'EOM': 'MD', 'n_steps': 100000, 'temperature': 300*unit.kelvin, 'ensemble': 'NPT', 'restraint_selection': 'protein and backbone' + to_exclude, 'force_constant': 1*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 1*unit.femtoseconds},
        {'EOM': 'MD', 'n_steps': 100000, 'temperature': 300*unit.kelvin, 'ensemble': 'NPT', 'restraint_selection': 'protein and backbone' + to_exclude, 'force_constant': 0.1*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 1*unit.femtoseconds},
        {'EOM': 'MD', 'n_steps': 249812500, 'temperature': 300*unit.kelvin, 'ensemble': 'NPT', 'restraint_selection': None, 'force_constant': 0*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 4*unit.femtoseconds},
    ]
else:
    print("Running normal equil", file=sys.stderr)
    stages = [
        {'EOM': 'minimize', 'n_steps': 0, 'temperature': 300*unit.kelvin, 'ensemble': 'NPT', 'restraint_selection': None, 'force_constant': 0*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 2*unit.femtoseconds},
        {'EOM': 'MD', 'n_steps': 250000000, 'temperature': 300*unit.kelvin, 'ensemble': 'NPT', 'restraint_selection': None, 'force_constant': 0*unit.kilocalories_per_mole/unit.angstrom**2, 'collision_rate': 2/unit.picoseconds, 'timestep': 4*unit.femtoseconds},
    ]

run_gentle_equilibration(topology, formatted_positions, system, stages)
