#!/usr/bin/env python
# coding: utf-8

import os
import sys
#from configparser import ConfigParser
from six.moves import configparser
import socket
import argparse
import lib.model_parse as mp
import cPickle as pickle


def parseArguments():

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--folder",
        help="The absolute path to the project file", type=str)
    parser.add_argument(
        "--final",
        dest='final', action='store_true')
    parser.add_argument(
        "--main_config", type=str,
        help="Config file", default="./configs/default.cfg")
    parser.add_argument(
        "--version", action="version",
        version='%(prog)s - Version 1.0')
    parser.add_argument(
        "--batch_size", dest='batch_size',
        type=int, default=300)
    parser.add_argument(
        "--ngpus",
        help="number of GPUs", default=1)
    parser.add_argument(
        "--model",
        help="name of the model to apply", default="")
    parser.add_argument(
        "--weights",
        help="weights of the model to apply", default="")
    args = parser.parse_args()
    return args


args = parseArguments()
parser = configparser.ConfigParser()
try:
    parser.read(args.main_config)
except Exception:
    raise Exception('Config File is missing!!!!')
backend = parser.get('Basics', 'keras_backend')
os.environ["KERAS_BACKEND"] = backend
cuda_path = parser.get('Basics', 'cuda_installation')
if not os.path.exists(cuda_path):
    raise Exception('Given Cuda installation does not exist!')
if cuda_path not in os.environ['LD_LIBRARY_PATH'].split(os.pathsep):
    print('Setting Cuda Path...')
    os.environ["PATH"] += os.pathsep + cuda_path
    os.environ['LD_LIBRARY_PATH'] += os.pathsep + cuda_path
    try:
        print('Attempt to Restart with new Cuda Path')
        os.execv(sys.argv[0], sys.argv)
    except Exception, exc:
        print 'Failed re-exec:', exc
        sys.exit(1)
if backend == 'theano':
    os.environ["THEANO_FLAGS"] = "mode=FAST_RUN,device=gpu,floatX=float32"

if backend == 'tensorflow':
    print('Run with backend Tensorflow')
    import tensorflow as tf
elif backend == 'theano':
    print('Run with backend Theano')
    import theano
else:
    raise NameError('Choose tensorflow or theano as keras backend')

import numpy as np
import keras
from keras.models import Sequential, load_model
import h5py
from lib.functions import generator
import math
import numpy.lib.recfunctions as rfn
from lib.functions import read_NN_weights

if __name__ == "__main__":

    # Process Command Line Arguments ######################################

    file_location = parser.get('Basics', 'thisfolder')
    mc_location = parser.get("Basics", "mc_path")

    args = parseArguments()
    print"\n ############################################"
    print("You are running the script with arguments: ")
    for a in args.__dict__:
        print(str(a) + ": " + str(args.__dict__[a]))
    print"############################################\n "

    # Load and Split the Datasets ######################################

    DATA_DIR = args.__dict__['folder']
    print('Make prediction for model in {}'.format(DATA_DIR))
    print DATA_DIR

    # with numpy dict
    run_info = np.load(os.path.join(DATA_DIR, 'run_info.npy'))[()]

    if run_info['Files'] == ['all']:
        input_files = os.listdir(mc_location)
    elif isinstance(run_info["Files"], list):
        input_files = run_info['Files']
    else:
        input_files = run_info['Files'].split(':')
    conf_model_file = os.path.join(DATA_DIR, args.model)
    print args.model
    print "##############################################################"
    test_inds = run_info["Test_Inds"]


    # create model (new implementation, functional API of Keras)
    print '##########################################################'
    print os.path.join(mc_location, input_files[0])
    base_model, inp_shapes, inp_trans, out_shapes, out_trans,_ = \
        mp.parse_functional_model(
            conf_model_file,
            os.path.join(mc_location, input_files[0]))

    ngpus = args.__dict__['ngpus']
#######################################################################################
    if args.__dict__["weights"] != '':
        args.__dict__["load_weights"] = os.path.join(DATA_DIR,
                                                     args.__dict__["weights"])
    else:
        args.__dict__["load_weights"] = os.path.join(DATA_DIR,
                                                     "best_val_loss.npy")
#####################################################################################
    print'Use {} GPUS'.format(ngpus)
    if ngpus > 1:
        model_serial = read_NN_weights(args.__dict__, base_model)
        model = multi_gpu_model(model_serial, gpus=ngpus)
    else:
        model = read_NN_weights(args.__dict__, base_model)
        model_serial = model
 
    os.system("nvidia-smi")

    # Saving the Final Model and Calculation/Saving of Result for Test Dataset ####

    file_handlers = [os.path.join(mc_location, file_name)
                     for file_name in input_files]
    t_c = 0
    while t_c < len(test_inds):
        if (test_inds[t_c][1]-test_inds[t_c][0])<=1:
            del test_inds[t_c]
            del file_handlers[t_c]
        else:
            t_c += 1
    

    num_events = np.sum([k[1] - k[0] for k in test_inds])
    print('Apply the NN to {} events'.format(num_events))
    steps_per_epoch = math.ceil(np.sum([k[1] - k[0] for k in
                                        test_inds]) / args.batch_size)
    if steps_per_epoch == 0:
        print "steps per epoch is 0, therefore manually set to 1"
        steps_per_epoch = 1

    prediction = model.predict_generator(
        generator(args.batch_size,
                  file_handlers,
                  test_inds,
                  inp_shapes,
                  inp_trans,
                  out_shapes,
                  out_trans),
        steps=steps_per_epoch,
        verbose=1,
        max_q_size=2)

    reference_outputs = mp.parse_reference_output(conf_model_file)
    #print('Reference output-vars: ', reference_outputs)
    ## add to first out-branch:
    outbranch0 = out_shapes.keys()[0]
    for ref_out in reference_outputs:
        out_shapes[outbranch0][ref_out] = 1
    mc_truth = [[] for br in out_shapes.keys()
                for var in out_shapes[br].keys()
                if var != 'general']
    IC_hit_vals = []
    DC_hit_vals = []
    reco_vals = None
    for i, file_handler_p in enumerate(file_handlers):
        down = test_inds[i][0]
        up = test_inds[i][1]
        file_handler = h5py.File(file_handler_p, 'r')
        temp_truth = file_handler['reco_vals'][down:up]
        for j, var in enumerate([var for br in out_shapes.keys()
                                for var in out_shapes[br].keys()
                                if var != 'general']):
            mc_truth[j].extend(temp_truth[var])
        if reco_vals == None:
            reco_vals = temp_truth
        else:
            reco_vals = np.concatenate([reco_vals, temp_truth])
        IC_hit_DOMs_list = []
        DC_hit_DOMs_list = []
        for k in xrange(up - down):
            IC_charge = file_handler["IC_charge"][down + k]
            DC_charge = file_handler["DC_charge"][down + k]
            IC_hitDOMs = np.count_nonzero(IC_charge)
            IC_hit_DOMs_list.append(IC_hitDOMs)
            DC_hitDOMs = np.count_nonzero(DC_charge)
            DC_hit_DOMs_list.append(DC_hitDOMs)
        IC_hit_vals.extend(IC_hit_DOMs_list)
        DC_hit_vals.extend(DC_hit_DOMs_list)
    dtype = np.dtype([(var + '_truth', np.float64)
                      for br in out_shapes.keys()
                      for var in out_shapes[br].keys()
                      if var != 'general'])
    mc_truth = np.array(zip(*np.array(mc_truth)), dtype=dtype)

    #write-out the mc_truth and the prediction separately to a joint pickle
    #file...we can also look for a nicer solution with dtypes again. but the
    #output-shape of prediction should be variable
    MANUAL_writeout_pred_and_exit = True
    save_name = args.__dict__["weights"][:-4] + "_pred.pickle"
    print save_name
    if MANUAL_writeout_pred_and_exit:
        pickle.dump({"mc_truth": mc_truth,
                     "prediction": prediction,
                     "reco_vals": reco_vals,
                     "IC_HitDOMs": IC_hit_vals,
                     "DC_HitDOMs": DC_hit_vals},
                    open(os.path.join(DATA_DIR, save_name), "wc"))
        print(' \n Finished .... Exiting.....')
        exit(0)
