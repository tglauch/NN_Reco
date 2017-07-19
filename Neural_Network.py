#!/usr/bin/env python
# coding: utf-8

import os
os.environ["THEANO_FLAGS"] = "mode=FAST_RUN,device=gpu,floatX=float32" 
os.environ["PATH"] += os.pathsep + '/usr/local/cuda/bin/'
import sys
import numpy as np
import theano
# theano.config.device = 'gpu'
# theano.config.floatX = 'float32'
import keras
from keras.models import Sequential, load_model
from keras.layers import Dense, Dropout, Activation, Flatten, Convolution2D,\
 BatchNormalization, MaxPooling2D,Convolution3D,MaxPooling3D
from keras.wrappers.scikit_learn import KerasRegressor
from keras.utils.io_utils import HDF5Matrix
from keras import regularizers
import h5py
import datetime
import gc
from configparser import ConfigParser
import argparse
import tables
import psutil
import math
import time
import resource

################# Function Definitions ####################################################################

def parseArguments():
  
  parser = argparse.ArgumentParser()
  parser.add_argument("--project", help="The name for the Project", type=str ,default='some_NN')
  parser.add_argument("--input", help="Name of the input files seperated by :", type=str ,default='all')
  parser.add_argument("--model", help="Name of the File containing the model", type=str, default='simple_CNN.cfg' )
  parser.add_argument("--virtual_len", help="Use an artifical array length (for debugging only!)", type=int , default=-1)
  parser.add_argument("--version", action="version", version='%(prog)s - Version 1.0')
    # Parse arguments
  args = parser.parse_args()
  return args

def add_layer(model, layer, args, kwargs):
    eval('model.add({}(*args,**kwargs))'.format(layer))
    return

def base_model(model_def):
  model = Sequential()
  with open('./simple_CNN.cfg') as f:
      args = []
      kwargs = dict()
      layer = ''
      mode = 'args'
      for line in f:
          cur_line = line.strip()
          if cur_line == '' and layer != '':
              add_layer(model, layer, args,kwargs)
              args = []
              kwargs = dict()
              layer = ''
          elif cur_line[0] == '#':
              continue
          elif cur_line == '[kwargs]':
              mode = 'kwargs'
          elif layer == '':
              layer = cur_line[1:-1]
          elif mode == 'args':
              try:
                  args.append(eval(cur_line.split('=')[1]))
              except:
                  args.append(cur_line.split('=')[1])
          elif mode == 'kwargs':
              split_line = cur_line.split('=')
              try:
                  kwargs[split_line[0].strip()] = eval(split_line[1].strip())
              except:
                  kwargs[split_line[0].strip()] = split_line[1].strip()
      if layer != '':
          add_layer(model, layer, args,kwargs)
    print(model.summary())

  adam = keras.optimizers.Adam(lr=0.001)
  model.compile(loss='mean_squared_error', optimizer=adam, metrics=['accuracy'])
  return model

class MemoryCallback(keras.callbacks.Callback):
    def on_epoch_end(self, epoch, log={}):
        print('RAM Usage'.format(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))

def generator(batch_size, input_data, out_data, inds):
  batch_input = np.zeros((batch_size, 1, 21, 21, 51))
  batch_out = np.zeros((batch_size,1))
  cur_file = 0
  cur_event_id = inds[cur_file][0]
  cur_len = 0
  up_to = inds[cur_file][1]
  while True:
    temp_in = []
    temp_out = []
    while cur_len<batch_size:
      fill_batch = batch_size-cur_len
      if fill_batch < (up_to-cur_event_id):
        temp_in.extend(input_data[cur_file][cur_event_id:cur_event_id+fill_batch])
        temp_out.extend(out_data[cur_file][cur_event_id:cur_event_id+fill_batch])
        cur_len += fill_batch
        cur_event_id += fill_batch
      else:
        temp_in.extend(input_data[cur_file][cur_event_id:up_to])
        temp_out.extend(out_data[cur_file][cur_event_id:up_to])
        cur_len += up_to-cur_event_id
        cur_file+=1
        if cur_file == len(inds):
          cur_file = 0
          cur_event_id = inds[cur_file][0]
          cur_len = 0
          up_to = inds[cur_file][1]
          break
        else:
          cur_event_id = inds[cur_file][0]
          up_to = inds[cur_file][1]
    print('{} | {}'.format(len(temp_in), len(temp_out)))
    for i in range(len(temp_in)):
      batch_input[i] = temp_in[i]
      batch_out[i] = np.log10(temp_out[i][0])
    cur_len = 0 
    yield (batch_input, batch_out)


if __name__ == "__main__":

#################### Process Command Line Arguments ######################################

  parser = ConfigParser()
  parser.read('config.cfg')
  file_location = parser.get('Basics', 'thisfolder')

  args = parseArguments()
  print"\n ############################################"
  print("You are running the script with arguments: ")
  for a in args.__dict__:
      print(str(a) + ": " + str(args.__dict__[a]))
  print"############################################\n "

  project_name = args.__dict__['project']

  if args.__dict__['input'] =='all':
    input_files = os.listdir(os.path.join(file_location, 'training_data/'))
  else:
    input_files = (args.__dict__['input']).split(':')

  
#################### Load and Split the Datasets ######################################  

  tvt_ratio=[float(parser.get('Training_Parameters', 'training_fraction')),
  float(parser.get('Training_Parameters', 'validation_fraction')),
  float(parser.get('Training_Parameters', 'test_fraction'))] 

  ## Create Folders
  folders=['train_hist/', 'train_hist/{}'.format(datetime.date.today())]
  for folder in folders:
      if not os.path.exists('{}'.format(os.path.join(file_location,folder))):
          os.makedirs('{}'.format(os.path.join(file_location,folder)))

  input_data = []
  out_data = []
  file_len = []

  for run, input_file in enumerate(input_files):
    data_file = os.path.join(file_location, 'training_data/{}'.format(input_file))

    if args.__dict__['virtual_len'] == -1:
      data_len = len(h5py.File(data_file)['charge'])
    else:
      data_len = args.__dict__['virtual_len']
      print('Only use the first {} Monte Carlo Events'.format(data_len))

    input_data.append(h5py.File(data_file, 'r')['charge'])
    out_data.append(h5py.File(data_file, 'r')['reco_vals'])
    file_len.append(data_len)

  train_frac  = float(tvt_ratio[0])/np.sum(tvt_ratio)
  valid_frac = float(tvt_ratio[1])/np.sum(tvt_ratio)
  train_inds = [(0, int(tot_len*train_frac)) for tot_len in file_len] 
  valid_inds = [(int(tot_len*train_frac), int(tot_len*(train_frac+valid_frac))) for tot_len in file_len] 
  test_inds = [(int(tot_len*(train_frac+valid_frac)), tot_len-1) for tot_len in file_len] 

  print(train_inds)
  print(valid_inds)
  print(test_inds)

#################### Train the Model #########################################################

  CSV_log = keras.callbacks.CSVLogger( \
    os.path.join(file_location,'./train_hist/{}/{}.csv'.format(datetime.date.today(), project_name)), 
    append=True)

  early_stop = keras.callbacks.EarlyStopping(\
    monitor='val_loss',
    min_delta = int(parser.get('Training_Parameters', 'delta')), 
    patience = int(parser.get('Training_Parameters', 'patience')), 
    verbose = int(parser.get('Training_Parameters', 'verbose')), 
    mode = 'auto')

  best_model = keras.callbacks.ModelCheckpoint(\
    os.path.join(file_location,'train_hist/{}/{}_best_val_loss.npy'.format(datetime.date.today(), project_name)), 
    monitor = 'val_loss', 
    verbose = int(parser.get('Training_Parameters', 'verbose')), 
    save_best_only = True, 
    mode='auto', 
    period=1)

  model = base_model(args.__dict__['model'])
  batch_size = int(parser.get('Training_Parameters', 'batch_size'))
  model.fit_generator(generator(batch_size, input_data, out_data, train_inds), 
                steps_per_epoch = math.ceil(np.sum([k[1]-k[0] for k in train_inds])/batch_size),
                validation_data = generator(batch_size, input_data, out_data, valid_inds),
                validation_steps = math.ceil(np.sum([k[1]-k[0] for k in valid_inds])/batch_size),
                callbacks = [CSV_log, early_stop, best_model, MemoryCallback()], 
                epochs = int(parser.get('Training_Parameters', 'epochs')), 
                verbose = int(parser.get('Training_Parameters', 'verbose')),
                max_q_size=int(parser.get('Training_Parameters', 'max_queue_size'))
                )


#################### Saving and Calculation of Result for Test Dataset ######################

  print('\n Save the Model \n')
  model.save(os.path.join(\
  file_location,'train_hist/{}/{}.h5'.format(datetime.date.today(),project_name)))  # save trained network

  print('\n Calculate Results... \n')
  res = []
  test_out = []

  for i in range(len(input_data)):
    print('Predict Values for {}'.format(input_file))
    test  = input_data[i][test_inds[i][0]:test_inds[i][1]]
    test_out_chunk = np.log10(out_data[i][test_inds[i][0]:test_inds[i][1],0:1])
    res_chunk= model.predict(test, verbose=int(parser.get('Training_Parameters', 'verbose')))
    res.extend(list(res_chunk))
    test_out.extend(list(test_out_chunk))


  np.save(os.path.join(file_location,'train_hist/{}/{}.npy'.format(datetime.date.today(), project_name)), 
    [res, np.squeeze(test_out)])

  print(' \n Finished .... ')
