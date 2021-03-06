#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Maxim Ziatdinov
"""

import h5py
import json
import numpy as np
import numpy.linalg as LA
import scipy.spatial as spatial
import cv2
from collections import OrderedDict

def open_hdf(filepath):
    """
    Opens a custom hdf5 file with STEM image and reads the key metadata
    
    Parameters:
    ----------
    filepath: string
        path to file with experimental data in hdf5 format

    Returns
    -------
    image_data: 2D or 3D numpy array
        Experimental image data
    metadata: json
        Metadata associated with the loaded image
        (scan size, microscope paramters, etc.)
    """
    with h5py.File(filepath, 'r') as f:
        image_data = f['image_data'][:]
        metadata = f['image_data'].attrs['metadata']
        metadata = json.loads(metadata)
        if image_data.ndim == 2:
            n_images = 1
            w, h = image_data.shape
            print_s = 'image of the size'
        else:
            n_images = image_data.shape[0]
            w, h = image_data.shape[1:3]
            print_s = 'images of the size'
        print("Loaded", n_images, print_s, w, 'by', h)
        print("Sample name:", metadata["sample name"])
        print("Type of experiment:", metadata["type of data"])
    return image_data, metadata

def open_library_hdf(filepath, *args, verbose=True):
    """
    Opens an hdf5 file with experimental image and defect coordinates
    
    Parameters:
    ----------
    filepath: string
        path to file with 'library' file in hdf5 format
    *args: dict
        dictionary with the types of lattice and dopant atoms
    verbose: boolean

    Returns
    -------
    image_data: 2D numpy array
        Experimental image data
    scan_size: float 
        Image size in picometers
    coordinates_all: numpy array
        nrows*3 array; the first two columns are x and y coordinates
        the third column shows atom type/class
    """
    try:
        atoms = args[0]
    except IndexError:
        atoms = None
    with h5py.File(filepath, 'r') as f:
        image_data = f['nn_input'][:]
        scan_size = f['nn_input'].attrs['scan size']
        if verbose:
            try:
                metadata = f['nn_input'].attrs['metadata']
            except KeyError:
                metadata = None
                print('No metadata found')
            if metadata is not None:
                metadata = json.loads(metadata)
                print("Sample name:", metadata["sample name"])
                print("Type of experiment:", metadata["type of data"])
                print("Sample growth -->")
                for k, v in metadata["sample growth"].items():
                    print("{}: ""{}".format(k, v))
        coordinates_all = np.empty((0, 3))
        for k in f.keys():
            if k != 'nn_input':
                coordinates = f[k].value
                coordinates = np.array(coordinates, dtype='U32')
                coordinates_all = np.append(coordinates_all, coordinates, axis=0)
    if atoms is not None:
        atomlist = coordinates_all[:, -1]
        xy = np.array(coordinates_all[:, :2], dtype=np.float)
        xy[:,[0, 1]] = xy[:,[1, 0]]
        atomlist[atomlist==atoms['lattice_atom']] = 0
        atomlist[atomlist==atoms['dopant']] = 1
        atomlist = np.array(atomlist, dtype=np.float)
        coordinates_all = np.concatenate((xy, atomlist[:, None]), axis=1)
        sort_idx = np.argsort(coordinates_all[:,-1])
        coordinates_all = coordinates_all[sort_idx]
    return image_data, scan_size, coordinates_all

def optimize_image_size(image_data, scan_size, px2ang=0.128, divisible_by=8):
    """
    Adjusts the size of input image for getting
    an optimal decoding result with a neural network

    Parameters:
    ----------
    image_data: 2D numpy array
        Experimental image
    scan_size: float
        Image size in picometers
    px2ang: float
        Optinal pixel-to-angstrom ratio
    divisible_by: int
        The resize image must be divisible by 2**n where
        n is a number of max-pooling layers used in a network

    Returns
    -------
    image_data: 2D numpy arra
        Resized image
    """
    if np.amax(image_data) > 255:
        image_data = image_data/np.amax(image_data)
    image_size = image_data.shape[0]
    px2ang_i = image_data.shape[0]/scan_size
    # get optimal image dimensions for nn-based decoding
    image_size_new = np.around(image_size * (px2ang/px2ang_i))
    while image_size_new % divisible_by != 0:
        px2ang_i -= 0.001
        image_size_new = np.around(image_size * (px2ang/px2ang_i))
    # resize image if necessary
    image_data = cv2.resize(image_data, (int(image_size_new), int(image_size_new)), cv2.INTER_CUBIC)
    print('Image resized to {} by {}'.format(int(image_size_new),int(image_size_new)))
    return image_data

def atom_bond_dict(atom1='C', atom2='Si',
                   bond11=('C', 'C', 175),
                   bond12=('Si', 'C', 210),
                   bond22=('Si', 'Si', 250)):
    """
    Returns type of host lattice atom, type of impurity atom
    and maximum bond lengths between each pair in the form of dictionaries
    """
    atoms = OrderedDict()
    atoms['lattice_atom'] = atom1
    atoms['dopant'] = atom2
    approx_max_bonds = {(bond11[0], bond11[1]): bond11[2],
                        (bond12[0], bond12[1]): bond12[2],
                        (bond22[0], bond22[1]): bond22[2]}
    return atoms, approx_max_bonds

def strainfunction(molecule_coord1, molecule_coord2, nnd_max=2):
    """
    Estimates strain, translation and rotation components
    for transforming one set of coordinates into another
    @Author: Xin Li CNMS/ORNL
    
    Parameters:
    ----------
    molecule_coord1: numpy array with shape nrows*2
        First set of coordinates
    molecule_coord2: numpy array with shape nrows*2
        Second set of coordinates

    Returns
    -------
    out: dict
        Ordered dictionary with calculated components of
        strain tensor, translation vecor and rotation vector
    """
    if len(molecule_coord1) != len(molecule_coord2):
        print('The defect structure is likely broken due to large strain',\
              'or you need to check a search radius')
        return
    points_ref = molecule_coord1.T
    points_tar = molecule_coord2.T
    n_points = points_tar.shape[1]
    y = points_tar.T.reshape(1,2*n_points).T
    M = np.zeros((2*n_points,6))
    for i in range(n_points):
        M[2*i:2*(i+1),:]=np.array(
            [[1,0,points_ref[0,i],points_ref[1,i],0,0],[0,1,0,0,points_ref[0,i],points_ref[1,i]]])
    x = np.linalg.pinv(M)@y
    t_est = x[0:2]
    F_est = x[2:].reshape(2,2)
    w,v = LA.eig(F_est.T@F_est)
    E_est = v.T@np.array([[np.sqrt(w[0]),0],[0,np.sqrt(w[1])]])@v
    R_est = F_est@np.linalg.inv(E_est)
    out = OrderedDict()
    out['translational vector'] = t_est
    out['strain_tensor'] = E_est
    out['rotation_matrix'] = R_est
    return out

def nn_atomdistance(coord):
    """
    Calculates nearest neighbor atomic distances

    Parameters:
    ----------
    coord: numpy array with shape nrows*3 or nrows*2
        The first two columns must be x and y coordinates

    Returns
    -------
    np.mean(distances_all): float
        Mean value of atomic nearest-neighbor distance
    np.std(distances_all): float
        Standard deviation value of atomic nearest-neighbor distances
    """
    distances_all = []
    checked_coord = []
    for i1, c in enumerate(coord[:,:2]):
        d, i2 = spatial.KDTree(coord[:,:2]).query(c, k=2)
        if tuple((i2[-1], i1)) not in checked_coord:
            checked_coord.append(tuple((i1, i2[-1])))
            distances_all.append(d[-1])
    return np.mean(distances_all), np.std(distances_all)
