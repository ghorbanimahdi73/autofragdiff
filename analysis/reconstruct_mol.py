"""
taken from TargetDiff
https://github.com/mattragoza/liGAN/blob/master/fitting.py
License: GNU General Public License v2.0
https://github.com/mattragoza/liGAN/blob/master/LICENSE
"""
import itertools

import numpy as np
from rdkit.Chem import AllChem as Chem
from rdkit import Geometry
from openbabel import openbabel as ob
from scipy.spatial.distance import pdist
from scipy.spatial.distance import squareform
import tempfile
from openbabel import openbabel
import tempfile

def write_xyz_file(coords, atom_types, filename):
    out = f"{len(coords)}\n\n"
    assert len(coords) == len(atom_types)
    for i in range(len(coords)):
        out += f"{atom_types[i]} {coords[i, 0]:.3f} {coords[i, 1]:.3f} {coords[i, 2]:.3f}\n"
    with open(filename, 'w') as f:
        f.write(out)

class MolReconsError(Exception):
    pass

"""
https://github.com/mattragoza/liGAN/blob/master/fitting.py

License: GNU General Public License v2.0
https://github.com/mattragoza/liGAN/blob/master/LICENSE
"""
import numpy as np
import itertools
from copy import deepcopy
import re

from rdkit.Chem import AllChem as Chem
from rdkit import Geometry
try:
    from openbabel import openbabel as ob
except ModuleNotFoundError:
    pass
from scipy.spatial.distance import pdist
from scipy.spatial.distance import squareform

class MolReconsError(Exception):
    pass

def reachable_r(a,b, seenbonds):
    '''Recursive helper.'''

    for nbr in ob.OBAtomAtomIter(a):
        bond = a.GetBond(nbr).GetIdx()
        if bond not in seenbonds:
            seenbonds.add(bond)
            if nbr == b:
                return True
            elif reachable_r(nbr,b,seenbonds):
                return True
    return False


def reachable(a,b):
    '''Return true if atom b is reachable from a without using the bond between them.'''
    if a.GetExplicitDegree() == 1 or b.GetExplicitDegree() == 1:
        return False #this is the _only_ bond for one atom
    #otherwise do recursive traversal
    seenbonds = set([a.GetBond(b).GetIdx()])
    return reachable_r(a,b,seenbonds)


def forms_small_angle(a,b,cutoff=45):
    '''Return true if bond between a and b is part of a small angle
    with a neighbor of a only.'''

    for nbr in ob.OBAtomAtomIter(a):
        if nbr != b:
            degrees = b.GetAngle(a,nbr)
            if degrees < cutoff:
                return True
    return False

def make_obmol(xyz, atomic_numbers):
    mol = ob.OBMol()
    mol.BeginModify()
    atoms = []
    for xyz,t in zip(xyz, atomic_numbers):
        x,y,z = xyz
        # ch = struct.channels[t]
        atom = mol.NewAtom()
        atom.SetAtomicNum(t)
        atom.SetVector(x,y,z)
        atoms.append(atom)
    return mol, atoms

def connect_the_dots(mol, atoms, indicators, covalent_factor=1.3):
    '''Custom implementation of ConnectTheDots.  This is similar to
    OpenBabel's version, but is more willing to make long bonds
    (up to maxbond long) to keep the molecule connected.  It also
    attempts to respect atom type information from struct.
    atoms and struct need to correspond in their order
    Assumes no hydrogens or existing bonds.
    '''

    """
    for now, indicators only include 'is_aromatic'
    """
    pt = Chem.GetPeriodicTable()

    if len(atoms) == 0:
        return

    mol.BeginModify()

    # just going to to do n^2 comparisons, can worry about efficiency later
    coords = np.array([(a.GetX(), a.GetY(), a.GetZ()) for a in atoms])
    dists = squareform(pdist(coords))
    # types = [struct.channels[t].name for t in struct.c]

    for i, j in itertools.combinations(range(len(atoms)), 2):
        a = atoms[i]
        b = atoms[j]
        a_r = ob.GetCovalentRad(a.GetAtomicNum()) * covalent_factor
        b_r = ob.GetCovalentRad(b.GetAtomicNum()) * covalent_factor
        if dists[i, j] < a_r + b_r:
            flag = 0
            if indicators and indicators[i] and indicators[j]:
                flag = ob.OB_AROMATIC_BOND
            mol.AddBond(a.GetIdx(), b.GetIdx(), 1, flag)

    atom_maxb = {}
    for (i, a) in enumerate(atoms):
        # set max valance to the smallest max allowed by openbabel or rdkit
        # since we want the molecule to be valid for both (rdkit is usually lower)
        maxb = min(ob.GetMaxBonds(a.GetAtomicNum()), pt.GetDefaultValence(a.GetAtomicNum()))

        if a.GetAtomicNum() == 16:  # sulfone check
            if count_nbrs_of_elem(a, 8) >= 2:
                maxb = 6

        # if indicators[i][ATOM_FAMILIES_ID['Donor']]:
        #     maxb -= 1 #leave room for hydrogen
        # if 'Donor' in types[i]:
        #     maxb -= 1 #leave room for hydrogen
        atom_maxb[a.GetIdx()] = maxb

    # remove any impossible bonds between halogens
    for bond in ob.OBMolBondIter(mol):
        a1 = bond.GetBeginAtom()
        a2 = bond.GetEndAtom()
        if atom_maxb[a1.GetIdx()] == 1 and atom_maxb[a2.GetIdx()] == 1:
            mol.DeleteBond(bond)

    def get_bond_info(biter):
        '''Return bonds sorted by their distortion'''
        bonds = [b for b in biter]
        binfo = []
        for bond in bonds:
            bdist = bond.GetLength()
            # compute how far away from optimal we are
            a1 = bond.GetBeginAtom()
            a2 = bond.GetEndAtom()
            ideal = ob.GetCovalentRad(a1.GetAtomicNum()) + ob.GetCovalentRad(a2.GetAtomicNum())
            stretch = bdist / ideal
            binfo.append((stretch, bond))
        binfo.sort(reverse=True, key=lambda t: t[0])  # most stretched bonds first
        return binfo

    binfo = get_bond_info(ob.OBMolBondIter(mol))
    # now eliminate geometrically poor bonds
    for stretch, bond in binfo:

        # can we remove this bond without disconnecting the molecule?
        a1 = bond.GetBeginAtom()
        a2 = bond.GetEndAtom()

        # as long as we aren't disconnecting, let's remove things
        # that are excessively far away (0.45 from ConnectTheDots)
        # get bonds to be less than max allowed
        # also remove tight angles, because that is what ConnectTheDots does
        if stretch > 1.2 or forms_small_angle(a1, a2) or forms_small_angle(a2, a1):
            # don't fragment the molecule
            if not reachable(a1, a2):
                continue
            mol.DeleteBond(bond)

    # prioritize removing hypervalency causing bonds, do more valent
    # constrained atoms first since their bonds introduce the most problems
    # with reachability (e.g. oxygen)
    hypers = [(atom_maxb[a.GetIdx()], a.GetExplicitValence() - atom_maxb[a.GetIdx()], a) for a in atoms]
    hypers = sorted(hypers, key=lambda aa: (aa[0], -aa[1]))
    for mb, diff, a in hypers:
        if a.GetExplicitValence() <= atom_maxb[a.GetIdx()]:
            continue
        binfo = get_bond_info(ob.OBAtomBondIter(a))
        for stretch, bond in binfo:

            if stretch < 0.9:  # the two atoms are too closed to remove the bond
                continue
            # can we remove this bond without disconnecting the molecule?
            a1 = bond.GetBeginAtom()
            a2 = bond.GetEndAtom()

            # get right valence
            if a1.GetExplicitValence() > atom_maxb[a1.GetIdx()] or a2.GetExplicitValence() > atom_maxb[a2.GetIdx()]:
                # don't fragment the molecule
                if not reachable(a1, a2):
                    continue
                mol.DeleteBond(bond)
                if a.GetExplicitValence() <= atom_maxb[a.GetIdx()]:
                    break  # let nbr atoms choose what bonds to throw out

    mol.EndModify()

def add_context(data):
    data.ligand_context_pos = data.ligand_pos
    data.ligand_context_element = data.ligand_element
    data.ligand_context_bond_index = data.ligand_bond_index
    data.ligand_context_bond_type = data.ligand_bond_type
    return data

def get_ring_sys(mol):
    all_rings = Chem.GetSymmSSSR(mol)
    if len(all_rings) == 0:
        ring_sys_list = []
    else:
        ring_sys_list = [all_rings[0]]
        for ring in all_rings[1:]:
            form_prev = False
            for prev_ring in ring_sys_list:
                if set(ring).intersection(set(prev_ring)):
                    prev_ring.extend(ring)
                    form_prev = True
                    break
            if not form_prev:
                ring_sys_list.append(ring)
    ring_sys_list = [list(set(x)) for x in ring_sys_list]
    return ring_sys_list

def fix_valence(mol):
    mol = deepcopy(mol)
    fixed = False
    cnt_loop = 0
    while True:
        try:
            Chem.SanitizeMol(mol)
            fixed = True
            break
        except Chem.rdchem.AtomValenceException as e:
            err = e
        except Exception as e:
            return mol, False # from HERE: rerun sample
        cnt_loop += 1
        if cnt_loop > 100:
            break
        N4_valence = re.compile(u"Explicit valence for atom # ([0-9]{1,}) N, 4, is greater than permitted")
        index = N4_valence.findall(err.args[0])
        if len(index) > 0:
            mol.GetAtomWithIdx(int(index[0])).SetFormalCharge(1)
    return mol, fixed

def get_all_subsets(ring_list):
    all_sub_list = []
    for n_sub in range(len(ring_list)+1):
        all_sub_list.extend(itertools.combinations(ring_list, n_sub))
    return all_sub_list

def fix_aromatic(mol, strict=False):
    mol_orig = mol
    atomatic_list = [a.GetIdx() for a in mol.GetAromaticAtoms()]
    N_ring_list = []
    S_ring_list = []
    for ring_sys in get_ring_sys(mol):
        if set(ring_sys).intersection(set(atomatic_list)):
            idx_N = [atom for atom in ring_sys if mol.GetAtomWithIdx(atom).GetSymbol() == 'N']
            if len(idx_N) > 0:
                idx_N.append(-1) # -1 for not add to this loop
                N_ring_list.append(idx_N)
            idx_S = [atom for atom in ring_sys if mol.GetAtomWithIdx(atom).GetSymbol() == 'S']
            if len(idx_S) > 0:
                idx_S.append(-1) # -1 for not add to this loop
                S_ring_list.append(idx_S)
    # enumerate S
    fixed = False
    if strict:
        S_ring_list = [s for ring in S_ring_list for s in ring if s != -1]
        permutation = get_all_subsets(S_ring_list)
    else:
        permutation = list(itertools.product(*S_ring_list))
    for perm in permutation:
        mol = deepcopy(mol_orig)
        perm = [x for x in perm if x != -1]
        for idx in perm:
            mol.GetAtomWithIdx(idx).SetFormalCharge(1)
        try:
            if strict:
                mol, fixed = fix_valence(mol)
            Chem.SanitizeMol(mol)
            fixed = True
            break
        except:
            continue
    # enumerate N
    if not fixed:
        if strict:
            N_ring_list = [s for ring in N_ring_list for s in ring if s != -1]
            permutation = get_all_subsets(N_ring_list)
        else:
            permutation = list(itertools.product(*N_ring_list))
        for perm in permutation:  # each ring select one atom
            perm = [x for x in perm if x != -1]
            # print(perm)
            actions = itertools.product([0, 1], repeat=len(perm))
            for action in actions: # add H or charge
                mol = deepcopy(mol_orig)
                for idx, act_atom in zip(perm, action):
                    if act_atom == 0:
                        mol.GetAtomWithIdx(idx).SetNumExplicitHs(1)
                    else:
                        mol.GetAtomWithIdx(idx).SetFormalCharge(1)
                try:
                    if strict:
                        mol, fixed = fix_valence(mol)
                    Chem.SanitizeMol(mol)
                    fixed = True
                    break
                except:
                    continue
            if fixed:
                break
    return mol, fixed


########## the following for reconstruct_from_generated (auto add bond) ##########

def reconstruct_from_generated(xyz, atomic_nums):
    #xyz = mol_info['atom_pos'].tolist()
    #atomic_nums = mol_info['element'].tolist()
    # xyz = data.ligand_context_pos.clone().cpu().tolist()
    # atomic_nums = data.ligand_context_element.clone().cpu().tolist()
    # indicators = data.ligand_context_feature_full[:, -len(ATOM_FAMILIES_ID):].clone().cpu().bool().tolist()

    mol, atoms = make_obmol(xyz, atomic_nums)
    fixup(atoms, mol, )

    connect_the_dots(mol, atoms,indicators=None, covalent_factor=1.3)
    fixup(atoms, mol, )
    mol.EndModify()

    fixup(atoms, mol, )

    mol.AddPolarHydrogens()
    mol.PerceiveBondOrders()
    fixup(atoms, mol, )

    for (i,a) in enumerate(atoms):
        ob.OBAtomAssignTypicalImplicitHydrogens(a)
    fixup(atoms, mol, )

    mol.AddHydrogens()
    fixup(atoms, mol, )

    #make rings all aromatic if majority of carbons are aromatic
    for ring in ob.OBMolRingIter(mol):
        if 5 <= ring.Size() <= 6:
            carbon_cnt = 0
            aromatic_ccnt = 0
            for ai in ring._path:
                a = mol.GetAtom(ai)
                if a.GetAtomicNum() == 6:
                    carbon_cnt += 1
                    if a.IsAromatic():
                        aromatic_ccnt += 1
            if aromatic_ccnt >= carbon_cnt/2 and aromatic_ccnt != ring.Size():
                #set all ring atoms to be aromatic
                for ai in ring._path:
                    a = mol.GetAtom(ai)
                    a.SetAromatic(True)

    #bonds must be marked aromatic for smiles to match
    for bond in ob.OBMolBondIter(mol):
        a1 = bond.GetBeginAtom()
        a2 = bond.GetEndAtom()
        if a1.IsAromatic() and a2.IsAromatic():
            bond.SetAromatic(True)
            
    mol.PerceiveBondOrders()

    rd_mol = convert_ob_mol_to_rd_mol(mol)

    # Post-processing
    rd_mol = postprocess_rd_mol_1(rd_mol)
    rd_mol = postprocess_rd_mol_2(rd_mol)

    return rd_mol


def fixup(atoms, mol, ):
    '''Set atom properties to match channel.  Keep doing this
    to beat openbabel over the head with what we want to happen.'''

    mol.SetAromaticPerceived(True)  #avoid perception
    for i, atom in enumerate(atoms):
        # ch = struct.channels[t]
        # ind = indicators[i]

        # if ind[ATOM_FAMILIES_ID['Aromatic']]:
        #     atom.SetAromatic(True)
        #     atom.SetHyb(2)

        # if ind[ATOM_FAMILIES_ID['Donor']]:
        #     if atom.GetExplicitDegree() == atom.GetHvyDegree():
        #         if atom.GetHvyDegree() == 1 and atom.GetAtomicNum() == 7:
        #             atom.SetImplicitHCount(2)
        #         else:
        #             atom.SetImplicitHCount(1) 


        # elif ind[ATOM_FAMILIES_ID['Acceptor']]: # NOT AcceptorDonor because of else
        #     atom.SetImplicitHCount(0)   

        if (atom.GetAtomicNum() in (7, 8)) and atom.IsInRing():     # Nitrogen, Oxygen
            #this is a little iffy, ommitting until there is more evidence it is a net positive
            #we don't have aromatic types for nitrogen, but if it
            #is in a ring with aromatic carbon mark it aromatic as well
            acnt = 0
            for nbr in ob.OBAtomAtomIter(atom):
                if nbr.IsAromatic():
                    acnt += 1
            if acnt > 1:
                atom.SetAromatic(True)


def convert_ob_mol_to_rd_mol(ob_mol,struct=None):
    '''Convert OBMol to RDKit mol, fixing up issues'''
    ob_mol.DeleteHydrogens()
    n_atoms = ob_mol.NumAtoms()
    rd_mol = Chem.RWMol()
    rd_conf = Chem.Conformer(n_atoms)

    for ob_atom in ob.OBMolAtomIter(ob_mol):
        rd_atom = Chem.Atom(ob_atom.GetAtomicNum())
        #TODO copy format charge
        if ob_atom.IsAromatic() and ob_atom.IsInRing() and ob_atom.MemberOfRingSize() <= 6:
            #don't commit to being aromatic unless rdkit will be okay with the ring status
            #(this can happen if the atoms aren't fit well enough)
            rd_atom.SetIsAromatic(True)
        i = rd_mol.AddAtom(rd_atom)
        ob_coords = ob_atom.GetVector()
        x = ob_coords.GetX()
        y = ob_coords.GetY()
        z = ob_coords.GetZ()
        rd_coords = Geometry.Point3D(x, y, z)
        rd_conf.SetAtomPosition(i, rd_coords)

    rd_mol.AddConformer(rd_conf)

    for ob_bond in ob.OBMolBondIter(ob_mol):
        i = ob_bond.GetBeginAtomIdx()-1
        j = ob_bond.GetEndAtomIdx()-1
        bond_order = ob_bond.GetBondOrder()
        if bond_order == 1:
            rd_mol.AddBond(i, j, Chem.BondType.SINGLE)
        elif bond_order == 2:
            rd_mol.AddBond(i, j, Chem.BondType.DOUBLE)
        elif bond_order == 3:
            rd_mol.AddBond(i, j, Chem.BondType.TRIPLE)
        else:
            raise Exception('unknown bond order {}'.format(bond_order))

        if ob_bond.IsAromatic():
            bond = rd_mol.GetBondBetweenAtoms (i,j)
            bond.SetIsAromatic(True)

    rd_mol = Chem.RemoveHs(rd_mol, sanitize=False)

    pt = Chem.GetPeriodicTable()
    #if double/triple bonds are connected to hypervalent atoms, decrement the order

    positions = rd_mol.GetConformer().GetPositions()
    nonsingles = []
    for bond in rd_mol.GetBonds():
        if bond.GetBondType() == Chem.BondType.DOUBLE or bond.GetBondType() == Chem.BondType.TRIPLE:
            i = bond.GetBeginAtomIdx()
            j = bond.GetEndAtomIdx()
            dist = np.linalg.norm(positions[i]-positions[j])
            nonsingles.append((dist,bond))
    nonsingles.sort(reverse=True, key=lambda t: t[0])

    for (d,bond) in nonsingles:
        a1 = bond.GetBeginAtom()
        a2 = bond.GetEndAtom()

        if calc_valence(a1) > pt.GetDefaultValence(a1.GetAtomicNum()) or \
           calc_valence(a2) > pt.GetDefaultValence(a2.GetAtomicNum()):
            btype = Chem.BondType.SINGLE
            if bond.GetBondType() == Chem.BondType.TRIPLE:
                btype = Chem.BondType.DOUBLE
            bond.SetBondType(btype)

    for atom in rd_mol.GetAtoms():
        #set nitrogens with 4 neighbors to have a charge
        if atom.GetAtomicNum() == 7 and atom.GetDegree() == 4:
            atom.SetFormalCharge(1)

    rd_mol = Chem.AddHs(rd_mol,addCoords=True)

    positions = rd_mol.GetConformer().GetPositions()
    center = np.mean(positions[np.all(np.isfinite(positions),axis=1)],axis=0)
    for atom in rd_mol.GetAtoms():
        i = atom.GetIdx()
        pos = positions[i]
        if not np.all(np.isfinite(pos)):
            #hydrogens on C fragment get set to nan (shouldn't, but they do)
            rd_mol.GetConformer().SetAtomPosition(i,center)

    try:
        Chem.SanitizeMol(rd_mol,Chem.SANITIZE_ALL^Chem.SANITIZE_KEKULIZE)
    except:
        raise MolReconsError()
    # try:
    #     Chem.SanitizeMol(rd_mol,Chem.SANITIZE_ALL^Chem.SANITIZE_KEKULIZE)
    # except: # mtr22 - don't assume mols will pass this
    #     pass
    #     # dkoes - but we want to make failures as rare as possible and should debug them
    #     m = pybel.Molecule(ob_mol)
    #     i = np.random.randint(1000000)
    #     outname = 'bad%d.sdf'%i
    #     print("WRITING",outname)
    #     m.write('sdf',outname,overwrite=True)
    #     pickle.dump(struct,open('bad%d.pkl'%i,'wb'))

    #but at some point stop trying to enforce our aromaticity -
    #openbabel and rdkit have different aromaticity models so they
    #won't always agree.  Remove any aromatic bonds to non-aromatic atoms
    for bond in rd_mol.GetBonds():
        a1 = bond.GetBeginAtom()
        a2 = bond.GetEndAtom()
        if bond.GetIsAromatic():
            if not a1.GetIsAromatic() or not a2.GetIsAromatic():
                bond.SetIsAromatic(False)
        elif a1.GetIsAromatic() and a2.GetIsAromatic():
            bond.SetIsAromatic(True)

    return rd_mol

UPGRADE_BOND_ORDER = {Chem.BondType.SINGLE:Chem.BondType.DOUBLE, Chem.BondType.DOUBLE:Chem.BondType.TRIPLE}

def postprocess_rd_mol_1(rdmol):

    rdmol = Chem.RemoveHs(rdmol)

    # Construct bond nbh list
    nbh_list = {}
    for bond in rdmol.GetBonds():
        begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx() 
        if begin not in nbh_list: nbh_list[begin] = [end]
        else: nbh_list[begin].append(end)
            
        if end not in nbh_list: nbh_list[end] = [begin]
        else: nbh_list[end].append(begin)

    # Fix missing bond-order
    for atom in rdmol.GetAtoms():
        idx = atom.GetIdx()
        num_radical = atom.GetNumRadicalElectrons()
        if num_radical > 0:
            for j in nbh_list[idx]:
                if j <= idx: continue
                nb_atom = rdmol.GetAtomWithIdx(j)
                nb_radical = nb_atom.GetNumRadicalElectrons()
                if nb_radical > 0:
                    bond = rdmol.GetBondBetweenAtoms(idx, j)
                    bond.SetBondType(UPGRADE_BOND_ORDER[bond.GetBondType()])
                    nb_atom.SetNumRadicalElectrons(nb_radical - 1)
                    num_radical -= 1
            atom.SetNumRadicalElectrons(num_radical)

        num_radical = atom.GetNumRadicalElectrons()
        if num_radical > 0:
            atom.SetNumRadicalElectrons(0)
            num_hs = atom.GetNumExplicitHs()
            atom.SetNumExplicitHs(num_hs + num_radical)
            
    return rdmol


def postprocess_rd_mol_2(rdmol):
    rdmol_edit = Chem.RWMol(rdmol)

    ring_info = rdmol.GetRingInfo()
    ring_info.AtomRings()
    rings = [set(r) for r in ring_info.AtomRings()]
    for i, ring_a in enumerate(rings):
        if len(ring_a) == 3:
            non_carbon = []
            atom_by_symb = {}
            for atom_idx in ring_a:
                symb = rdmol.GetAtomWithIdx(atom_idx).GetSymbol()
                if symb != 'C':
                    non_carbon.append(atom_idx)
                if symb not in atom_by_symb:
                    atom_by_symb[symb] = [atom_idx]
                else:
                    atom_by_symb[symb].append(atom_idx)
            if len(non_carbon) == 2:
                rdmol_edit.RemoveBond(*non_carbon)
            if 'O' in atom_by_symb and len(atom_by_symb['O']) == 2:
                rdmol_edit.RemoveBond(*atom_by_symb['O'])
                rdmol_edit.GetAtomWithIdx(atom_by_symb['O'][0]).SetNumExplicitHs(
                    rdmol_edit.GetAtomWithIdx(atom_by_symb['O'][0]).GetNumExplicitHs() + 1
                )
                rdmol_edit.GetAtomWithIdx(atom_by_symb['O'][1]).SetNumExplicitHs(
                    rdmol_edit.GetAtomWithIdx(atom_by_symb['O'][1]).GetNumExplicitHs() + 1
                )
    rdmol = rdmol_edit.GetMol()

    for atom in rdmol.GetAtoms():
        if atom.GetFormalCharge() > 0:
            atom.SetFormalCharge(0)

    return rdmol
    

def calc_valence(rdatom):
    '''Can call GetExplicitValence before sanitize, but need to
    know this to fix up the molecule to prevent sanitization failures'''
    cnt = 0.0
    for bond in rdatom.GetBonds():
        cnt += bond.GetBondTypeAsDouble()
    return cnt


def count_nbrs_of_elem(atom, atomic_num):
    '''
    Count the number of neighbors atoms
    of atom with the given atomic_num.
    '''
    count = 0
    for nbr in ob.OBAtomAtomIter(atom):
        if nbr.GetAtomicNum() == atomic_num:
            count += 1
    return count

def make_mol_openbabel(positions, atom_types, atom_decoder):
    """
    Build an RDKit molecule using openbabel for creating bonds
    Args:
        positions: N x 3
        atom_types: N
        atom_decoder: maps indices to atom types
    Returns:
        rdkit molecule
    """
    atom_types = [atom_decoder[x] for x in atom_types]

    with tempfile.NamedTemporaryFile() as tmp:
        tmp_file = tmp.name

        # Write xyz file
        write_xyz_file(positions, atom_types, tmp_file)

        # Convert to sdf file with openbabel
        # openbabel will add bonds
        obConversion = openbabel.OBConversion()
        obConversion.SetInAndOutFormats("xyz", "sdf")
        ob_mol = openbabel.OBMol()
        obConversion.ReadFile(ob_mol, tmp_file)

        obConversion.WriteFile(ob_mol, tmp_file)

        # Read sdf file with RDKit
        tmp_mol = Chem.SDMolSupplier(tmp_file, sanitize=False)[0]

    # Build new molecule. This is a workaround to remove radicals.
    mol = Chem.RWMol()
    for atom in tmp_mol.GetAtoms():
        mol.AddAtom(Chem.Atom(atom.GetSymbol()))
    mol.AddConformer(tmp_mol.GetConformer(0))

    for bond in tmp_mol.GetBonds():
        mol.AddBond(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx(),
                    bond.GetBondType())

    return mol