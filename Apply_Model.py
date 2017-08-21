#!/usr/bin/env python
# coding: utf-8

import os
import sys
from configparser import ConfigParser
import socket

print('Running on Hostcomputer {}'.format(socket.gethostname()))

parser = ConfigParser()
try:
  parser.read('config.cfg')
except:
  raise Exception('Config File is missing!!!!')  

backend = parser.get('Basics', 'keras_backend')
os.environ["KERAS_BACKEND"] = backend
if backend == 'theano':
    os.environ["THEANO_FLAGS"] = "mode=FAST_RUN,device=gpu,floatX=float32" 

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

if backend == 'tensorflow':
  print('Run with backend Tensorflow')
  import tensorflow as tf
elif backend == 'theano':
  print('Run with backend Theano')
  import theano
else:
  raise NameError('Choose tensorflow or theano as keras backend')

import numpy as np
import theano
import keras
from keras.models import Sequential, load_model
import argparse
from configparser import ConfigParser
import h5py
import shelve
from functions import generator, base_model, parse_config_file, prepare_input_output_variables
import math



def parseArguments():

  parser = argparse.ArgumentParser()
  parser.add_argument("--folder", help="The path to the project folder", type=str)
  parser.add_argument("--final", dest='final', action='store_true')
  parser.add_argument("--batch_size", dest='batch_size', type=int, default=500)
  parser.add_argument("--version", action="version", version='%(prog)s - Version 1.0')
    # Parse arguments
  args = parser.parse_args()
  return args

if __name__ == "__main__":

#################### Process Command Line Arguments ######################################

  parser = ConfigParser()
  parser.read('config.cfg')
  file_location = parser.get('Basics', 'thisfolder')
  mc_location = './training_data/'

  args = parseArguments()
  print"\n ############################################"
  print("You are running the script with arguments: ")
  for a in args.__dict__:
      print(str(a) + ": " + str(args.__dict__[a]))
  print"############################################\n "
  
#################### Load and Split the Datasets ######################################  

  DATA_DIR = os.path.join(file_location, args.__dict__['folder'])
  print('Make prediction for model in {}'.format(DATA_DIR))
  shelf = shelve.open(os.path.join(DATA_DIR, 'run_info.shlf'))

  if shelf['Files']=='all':
    input_files = os.listdir(os.path.join(file_location, 'training_data/'))
  else:
  	input_files = (shelf['Files']).split(':')

  model_settings, model_def = parse_config_file(os.path.join(DATA_DIR, 'model.cfg'))
  shapes, shape_names, inp_variables, inp_transformations, out_variables, out_transformations = \
     prepare_input_output_variables(os.path.join(mc_location, input_files[0]), model_settings)
  model = base_model(model_def, shapes, shape_names)
  if args.__dict__['final']:
  	model.load_weightsl(os.path.join(DATA_DIR,'final_network.h5'))
  else:
  	model.load_weights(os.path.join(DATA_DIR,'best_val_loss.npy'))  	
  res = []
  test_out = []

  file_handlers = [h5py.File(os.path.join(mc_location, file_name), 'r') for file_name in input_files]
  test_inds =   shelf['Test_Inds']
  print(test_inds)
  print('##########')
  prediction = model.predict_generator(generator(args.batch_size, file_handlers, test_inds, shapes, inp_variables,\
   inp_transformations, out_variables, out_transformations), 
                steps = math.ceil(np.sum([k[1]-k[0] for k in test_inds])/args.batch_size),
                verbose = 1,
                max_q_size = 2
                )
 
  mc_truth = [[]*len(out_variables)]
  for i, file_handler in enumerate(file_handlers):
    down = test_inds[i][0]
    up = test_inds[i][1]
    temp_truth = file_handler['reco_vals'][down:up]
    for j, var in enumerate(out_variables):
      mc_truth[j].extend(temp_truth[var])

  dtype = [(var, np.float64) for var in out_variables]
  mc_truth = np.array(mc_truth, dtype=dtype)



  np.save(os.path.join(DATA_DIR, 'test_res.npy'), [mc_truth, prediction])