
# coding: utf-8

# <h1>TACOTRON</h1>

# In[1]:


from __future__ import print_function
import mxnet as mx
import numpy as np
from mxnet import nd, autograd

ctx= mx.gpu(0)

import csv
import codecs
import re
import audio_process
import datetime
import os
from os.path import expanduser
import math
import logging
from params import Hyperparams as hp

import time

logging.getLogger().setLevel(logging.DEBUG)


# <h3> DATA SETUP </h3>
#
# <b>Data</b>:
# text - mel spectrograms - linear spectrograms
#
# <b>Shapes</b>:
# (batch_size, pad_to_max_text_length) - (batch_size, 80, pad_to_max_audio_length) - (batch_size, 1025, pad_to_max_audio_length)
#
# <b>Note_1</b>: I'm using a little batch size due my little dummy train dataset <br/>
# <b>Note_2</b>: on Tensorflow implementation there is a reshape step by reduction factor r described in the paper.<br/> Tensorflow data got these shapes:
#
# <b>text</b>:
# (batch_size,length_text)
# <b>mel spectrograms</b>:
# (batch_size, time_frames, 80&ast;r)
# <b>linear spectrograms</b>:
# (batch_size, time_frames, 1025&ast;r)
#
# <br/>
# more info at: https://github.com/Kyubyong/tacotron/blob/master/utils.py#L58

# In[2]:


def generate_vocabulary(texts_list):
    # get unique chars and put into a list
    return list(set(''.join(texts_list)))


def generate_chars2numbers_mappings(vocabulary):
    # create a chars <-> numbers mappings
    char2index = {char:i for i,char in enumerate(vocabulary)}
    index2char = {i:char for i,char in enumerate(vocabulary)}

    return char2index,index2char


def text2numbers(texts_list,char2index_mapping):
    numerical_texts=[]
    for text in texts_list:
        numerical_texts.append([char2index_mapping[char] for char in text])
    return numerical_texts

def open_data(input_file_path):

    texts, sound_files = [], []

    reader = csv.reader(codecs.open(input_file_path, 'rb', 'utf-8'))
    for row in reader:
        sound_filename, text, _ = row
        sound_file = hp.sound_fpath +"/"+ sound_filename + ".wav"
        text = re.sub(r"[^ a-z']", "", text.strip().lower())

        texts.append(text)
        sound_files.append(sound_file)

    return texts, sound_files
# Returns: one-hot-encoded-text, linear spectrum, mel spectrum
# Shapes: (data_length, ?, ?) , (data_length, (n_fft/2)+1, ceil(max_audio_length/hop_size)), (data_length, n_mels, ceil(max_audio_length/hop_size))
def generate_text_spectra(texts_list, sound_labels):

    assert len(sound_labels) == len(texts_list)

    print("Generating spectrograms")
    print("Sample length for windowing:",hp.win_length)
    print("Sample length for hop:",hp.hop_length,"\n")
    #tuples of wav and sr of that wav. wav is a 1D floats vector
    wavs_srs = [audio_process.load_wave(sound_clip) for sound_clip in sound_labels]
    longest_wav_sr = (max(wavs_srs, key= lambda wav: len(wav[0])))
    #save the longest audio file length
    max_samples_length=(len(longest_wav_sr[0]))
    print("max audio sample length:",max_samples_length)

    #prepare the data structure for save all the spectra
    spectra_lin = []#mx.ndarray.zeros((len(sound_labels),math.ceil(max_samples_length/hp.hop_length),1+(hp.n_fft//2)))
    spectra_mel = []#mx.ndarray.zeros((len(sound_labels),math.ceil(max_samples_length/hp.hop_length),hp.n_mels))
    mel_basis = audio_process.get_mel_basis()
    print("Padding audio and compute mel and lin spectra..")
    for indx,wav_sr in enumerate(wavs_srs):
        wav = wav_sr[0]
        wav_length = len(wav)
#         print("wav l",w_length)
        diff = max_samples_length-wav_length
#         print("num of zeros to add",diff)
        padded = np.append(wav,np.zeros(diff))
        # get the spectrum from the padded sound
        spectrum_lin, spectrum_mel=audio_process.do_spectrograms(y=padded)
#         print(padded_spectrum_lin.shape)
        # save into the ndarray
        spectra_lin.append(np.transpose(spectrum_lin))#[indx,:,:]=np.transpose(spectrum_lin[:,:])
        spectra_mel.append(np.transpose(spectrum_mel))#[indx,:,:]=np.transpose(spectrum_mel[:,:])



    texts_one_hot=None
    if hp.do_text_processing:
        print("Processing text..")
        vocabulary = generate_vocabulary(texts_list)
        vocab_size=len(vocabulary)
        char2index,index2char = generate_chars2numbers_mappings(vocabulary)

        print("Converting text to integers..")
        texts_numerical = text2numbers(texts_list,char2index)
        # simulate a different sequence length
    #   /D E L E T E M E/
        texts_numerical[4]=np.concatenate((texts_numerical[4],[8,9]))
    #   /D E L E T E M E/

        longest_sequence = (max(texts_numerical, key= lambda seq: len(seq)))
        longest_sequence_len=len(longest_sequence)
        print("Pad sequences to",longest_sequence_len,"..")
        # helper function for the lambda expression
        def _padseq(seq,max_len):
            diff=max_len-len(seq)
            if diff>0:
                # SHITTY USELESS MXNET API. CANNOT CONCAT A NON-EMPTY WITH EMPTY ARRAY.
                # EDIT: use numpy now. Still using this condition for safety
                pad = np.zeros(diff)-1
                seq=np.append(seq,[pad])
            return seq

        padded_sequences = mx.nd.array(
            list(
                map(
                    lambda seq: _padseq(seq,longest_sequence_len), texts_numerical
                )
            )
        )

        texts_one_hot=mx.ndarray.one_hot(padded_sequences,vocab_size)

    return texts_one_hot, mx.nd.array(spectra_lin), mx.nd.array(spectra_mel)


# In[3]:



#class NDArrayIter_NTC(mx.io.NDArrayIter):

#    @property
#    def provide_data(self):
#        """The name and shape of data provided by this iterator."""
#        return [
#            mx.io.DataDesc(k, tuple([self.batch_size] + list(v.shape[1:])), v.dtype, layout="NTC")
#            for k, v in self.data
#        ]



# In[4]:


def get_iterators():
    texts_list, sound_files_list = open_data(hp.csv_file)
    size=len(sound_files_list)

    texts_one_hot, spectra_lin, spectra_mel = generate_text_spectra(texts_list, sound_files_list)

    # get 10% of dataset as eval data
    eval_indxs = (np.random.randint(0, high=size, size=size//10))
    #eval_indxs=[32 10 28 19 29]
    # remaining indexes for the train
    train_indxs = np.setdiff1d(np.arange(size),eval_indxs)

    print("I will take those for eval:",eval_indxs)
    print("..and the remaining for train:",train_indxs,"\n")

    #take from the array (1st arg) the indexes of the first dimension specified by the 2nd arg
    #train_txt take the one_hot matrices

    if hp.do_text_processing:
        train_txt_data = mx.ndarray.take(texts_one_hot,mx.nd.array(train_indxs))
        eval_txt_data = mx.ndarray.take(texts_one_hot,mx.nd.array(eval_indxs))

    train_data = mx.ndarray.take(spectra_mel,mx.nd.array(train_indxs))
    train_label = mx.ndarray.take(spectra_lin,mx.nd.array(train_indxs))

    eval_data = mx.ndarray.take(spectra_mel,mx.nd.array(eval_indxs))
    eval_label = mx.ndarray.take(spectra_lin,mx.nd.array(eval_indxs))

    print("train data shape:",train_data.shape,"train label shape:",train_label.shape)
    print("eval data shape:", eval_data.shape,"eval label shape:",eval_label.shape,"\n")


    try:
        print("Populating traindata iterator")
        traindata_iterator = mx.io.NDArrayIter(data={'mel_spectrogram':train_data},
                                label={'linear_spectrogram':train_label},
                                batch_size=hp.batch_size,
                                shuffle=True)
        print("Populating evaldata iterator")
        evaldata_iterator = mx.io.NDArrayIter(data={'mel_spectrogram':eval_data},
                                label={'linear_spectrogram':eval_label},
                                batch_size=hp.batch_size)
    except Exception as e:
        print(e)
        traceback.print_exc()

#     for batch in traindata_iterator:
#         print(batch.data[0].asnumpy())
#         print(batch.data[0].shape)

    return traindata_iterator,evaldata_iterator, train_data.shape[1],eval_data,eval_label


# <h3> Modules </h3>

# <h4> Prenet </h4>

# In[5]:


"""
FC-256-ReLU → Dropout(0.5) → FC-128-ReLU → Dropout(0.5)
"""
def prenet_pass(data):
    fc1 = mx.symbol.FullyConnected(data=data, num_hidden=hp.emb_size, name='prenet_fc1',flatten=False)
    act1 = mx.symbol.Activation(data=fc1, act_type='relu', name='prenet_act1')
    drop1 = mx.symbol.Dropout(act1, p=0.5, name='prenet_drop1')

    fc2 = mx.symbol.FullyConnected(data=drop1, num_hidden=hp.emb_size//2, name='prenet_fc2', flatten=False)
    act2 = mx.symbol.Activation(data=fc2, act_type='relu', name='prenet_act2')
    prenet_output = mx.symbol.Dropout(act2, p=0.5, name='prenet_drop2')

    return prenet_output


# <h4> Convolution 1D Bank </h4>

# In[6]:


# Convolution bank of K filter
def conv1dBank(conv_input, K): # 1,88,128 # N C W -> 1 80 88
    #(32,88,128)
    #N C W (num_batch, channel, width)

    #The k-th filter got a kernel width of k, with 0<k<=K
    conv=mx.sym.Convolution(data=conv_input, kernel=(1,), num_filter=hp.emb_size//2,name="convBank_1")
    #(32,128,128) ==> (32,K*128,128)
    #(32,num_filter,out_width)

    conv = mx.sym.Activation(data=conv, act_type='relu')
    '''
    BatchNorm: Got error out_grad.size() check failed 1==3 using GPU during fit()
    '''
    #(conv, mean, var) = mx.sym.BatchNorm(data=conv, output_mean_var=True)
    if hp.use_convBank_batchNorm:
        conv = mx.sym.BatchNorm(data=conv, name="batchN_bank_1")

    for k in range(2, K+1):
        in_i = mx.sym.concat(conv_input,mx.sym.zeros((hp.batch_size,hp.emb_size//2,k-1)),dim=2)
        convi = mx.sym.Convolution(data=in_i, kernel=(k,), num_filter=hp.emb_size//2,name="convBank_"+str(k))

        convi = mx.sym.Activation(data=convi,act_type='relu')
        '''
        BatchNorm: Got error out_grad.size() check failed 1==3 using GPU during fit()
        '''

        #(convi, mean, var) = mx.sym.BatchNorm(data=convi, output_mean_var=True)
        if hp.use_convBank_batchNorm:
            convi = mx.sym.BatchNorm(data=convi, name='batchN_bank_'+str(k))
        conv = mx.symbol.concat(conv,convi,dim=1)

    return conv


# <h4> Highway </h4>

# In[7]:


# highway
def highway_layer(data,i=0):
    H= mx.symbol.Activation(
        data=mx.symbol.FullyConnected(data=data, num_hidden=hp.emb_size//2, name="highway_fcH_"+str(i),flatten=False),
        act_type="relu"
    )
    T= mx.symbol.Activation(
        data=mx.symbol.FullyConnected(data=data, num_hidden=hp.emb_size//2, bias=mx.sym.Variable('bias'+str(i),init=mx.initializer.Normal()), name="highway_fcT"+str(i),flatten=False),
        act_type="sigmoid"
    )
    return  H * T + data * (1.0 - T)


# <h4> CBHG </h4>

# In[8]:


# CBHG
def CBHG(data,K,proj1_size,proj2_size,num_unroll):
    bank = conv1dBank(data,K)

    #After the convolutional bank, a max pooling is applied
    #Again here. To obtain always the same dimension I'm padding the input of each operation
    conv_padded = mx.sym.concat(bank,mx.sym.zeros((hp.batch_size,K*(hp.emb_size//2),1)),dim=2)
    poold_bank = mx.sym.Pooling(data=conv_padded, pool_type='max', kernel=(2,), stride=(1,), name="CBHG_pool")
    #(32,1024,127)
    #Now two other projections (convolutions) are done. Same padding thing
    poold_bank_padded = mx.sym.concat(poold_bank,mx.sym.zeros((hp.batch_size,K*(hp.emb_size//2),2)),dim=2)

    proj1 = mx.sym.Convolution(data=poold_bank_padded, kernel=(3,), num_filter=proj1_size, name='CBHG_conv1')
    proj1 = mx.sym.Activation(data=proj1, act_type='relu', name='CBHG_act1')

    if hp.use_proj1_batchNorm:
        proj1 = mx.sym.BatchNorm(data=proj1, name="batchNorm_proj1")

    proj1_padded = mx.sym.concat(proj1,mx.sym.zeros((hp.batch_size,hp.emb_size,2)),dim=2)
    proj2 = mx.sym.Convolution(proj1_padded, kernel=(3,), num_filter=proj2_size, name='CBHG_conv2')

    if hp.use_proj2_batchNorm:
        proj2=mx.sym.BatchNorm(data=proj2, name="batchNorm_proj2")

    #Adding residual connection. The output of the prenet pass is added to proj2
    residual= proj2 + data

    residual = mx.sym.swapaxes(residual,1,2)

    #A 4 highway layers is created
    for i in range(4):
        residual = highway_layer(residual,i)
    highway_pass = residual

    #The highway output is passed to the bidirectional gru cell
    bidirectional_gru_cell = mx.rnn.BidirectionalCell(
        mx.rnn.GRUCell(num_hidden=hp.emb_size//2, prefix='CBHG_gru1'),
        mx.rnn.GRUCell(num_hidden=hp.emb_size//2, prefix='CBHG_gru2'),
        output_prefix='CBHG_bi_'
    )

    bi_gru_outputs, bi_gru_states = bidirectional_gru_cell.unroll(num_unroll, inputs=highway_pass, merge_outputs=True)

    return bi_gru_outputs


# <h4> Encoder </h4>

# In[9]:


# encoder
def encoder(data):
    embed_vector = mx.sym.Embedding(data=data, input_dim=longest_word, output_dim=hp.emb_size, name='encoder_embed')
    prenet_output = prenet_pass(embed_vector)
    return CBHG(prenet_output,16, hp.emb_size//2, hp.emb_size//2)


# <h4> Decoder (stub)</h4>

# In[10]:


# decoder
def decoder(input_spectrogram,context,reduction_factor):
    #embed_vector = mx.sym.Embedding(data=input_spectrogram, input_dim=80, output_dim=emb_size, name='decoder_embed')
    prenet_output = prenet_pass(input_spectrogram)

    stack = mx.rnn.SequentialRNNCell()
    stack.add(mx.rnn.GRUCell(num_hidden=hp.emb_size,prefix='decoder_layer1_'))
    stack.add(mx.rnn.GRUCell(num_hidden=hp.emb_size,prefix='decoder_layer2_'))

    residual_gru_stack = mx.rnn.ResidualCell(stack)

    gru_outputs,states = residual_gru_stack.unroll(length=1,
                                               inputs=prenet_output,
                                               begin_state=context,
                                               merge_outputs=True)

    predicted_frames = mx.symbol.Activation(
        data=mx.symbol.FullyConnected(data=gru_outputs, num_hidden=80*reduction_factor),
        act_type="relu"
    )

    return predicted_frames, states


# In[11]:


def postprocess(input_mel_spectrograms,max_audio_length):
    in_cbhg = prenet_pass(input_mel_spectrograms)
    in_cbhg_sw= mx.sym.swapaxes(in_cbhg,1,2)

    bi_gru_out =CBHG(in_cbhg_sw,hp.post_process_num_banks,hp.emb_size,hp.emb_size//2,max_audio_length)

    linear_scale_spectrograms = mx.symbol.FullyConnected(data=bi_gru_out,num_hidden=(hp.n_fft//2)+1,flatten=False)
    return linear_scale_spectrograms


# In[12]:


np.random.seed(2) #[42 24  3  8  0]
traindata_iterator, evaldata_iterator, max_audio_length,eval_data,eval_label = get_iterators()
linear_spectrogram = mx.sym.Variable('linear_spectrogram')
mel_spectrogram = mx.sym.Variable('mel_spectrogram')
print("max_audio_length: ",max_audio_length)


# In[13]:


#traindata_iterator.provide_data


# In[14]:


net = mx.sym.MAERegressionOutput(data=postprocess(mel_spectrogram,max_audio_length), label=linear_spectrogram)
#net = mx.sym.SoftmaxOutput(data=postprocess(mel_spectrogram,max_audio_length), label=linear_spectrogram)
model = mx.mod.Module(symbol=net,
                      context=ctx,
                      data_names=['mel_spectrogram'],
                      label_names=['linear_spectrogram']
                     )
# model.bind(for_training=False, data_shapes= traindata_iterator.provide_data, label_shapes=traindata_iterator.provide_label)
# model.load("/home/stefano/CBHG_model/tacotron_15119700453363569/prefix")


# In[15]:

checkpoints_dir = expanduser("~")+"/results/CBHG_model/"+hp.dataset_name+"/"+"".join(str(time.time()).split('.'))
prefix = hp.dataset_name
checkpoint_period = hp.num_epochs//2;
if checkpoint_period < 1:
    checkpoint_period=1

if not os.path.exists(checkpoints_dir):
    print("Creating directory for checkpoints")
    os.makedirs(checkpoints_dir)
print("Start training:")
print("- Dataset name:",hp.dataset_name)
print("- Dataset csv file:",hp.csv_file)
print("- Batch size:",hp.batch_size)
print("- Epochs:",hp.num_epochs)
print("- Checkpoint period:",checkpoint_period)
print("- Conv1DBank. use batch normalization:",hp.use_convBank_batchNorm)
print("- Projection_1. use batch normalization:",hp.use_proj1_batchNorm)
print("- Projection_2. use batch normalization:",hp.use_proj2_batchNorm)

model.fit(
        traindata_iterator,
        eval_data=evaldata_iterator,
        optimizer=mx.optimizer.Adam(rescale_grad=1/hp.batch_size),
        optimizer_params={'learning_rate': 0.0001, 'momentum': 0.9},
        eval_metric='mae',
        batch_end_callback = mx.callback.Speedometer(hp.batch_size, 10),
        epoch_end_callback = mx.callback.do_checkpoint(checkpoints_dir+"/"+prefix,checkpoint_period),
        num_epoch=hp.num_epochs
)


# In[22]:


'''
Load first checkpoint n: num_epochs/2
'''
sym_1, arg_params_1, aux_params_1 = mx.model.load_checkpoint(checkpoints_dir+"/"+prefix, hp.num_epochs//2)
model_1chkpnt = mx.mod.Module(symbol=sym_1, context=ctx,data_names=['mel_spectrogram'],label_names=['linear_spectrogram'])

model_1chkpnt.bind(for_training=False, data_shapes= traindata_iterator.provide_data, label_shapes=traindata_iterator.provide_label)
# assign the loaded parameters to the module

model_1chkpnt.set_params(arg_params_1, aux_params_1)

'''
Save waveforms of predicted data
'''
predictions_1 =model.predict(evaldata_iterator)

for i,predicted_spectr in enumerate(predictions_1):
    y = audio_process.inv_spectrogram(np.transpose(predicted_spectr.asnumpy()))
    audio_process.save_wave(checkpoints_dir+"/"+prefix+"_checkpoint1_"+str(i),y,hp.sr)
#


# In[21]:


'''
Load second checkpoint n: num_epochs
'''
sym_2, arg_params_2, aux_params_2 = mx.model.load_checkpoint(checkpoints_dir+"/"+prefix, hp.num_epochs)
model_2chkpnt = mx.mod.Module(symbol=sym_2, context=ctx,data_names=['mel_spectrogram'],label_names=['linear_spectrogram'])

model_2chkpnt.bind(for_training=False, data_shapes= traindata_iterator.provide_data, label_shapes=traindata_iterator.provide_label)
# assign the loaded parameters to the module

model_2chkpnt.set_params(arg_params_2, aux_params_2)

'''
Save waveforms of predicted data
'''
predictions_2 =model.predict(evaldata_iterator)

for i,predicted_spectr in enumerate(predictions_2):
    y = audio_process.inv_spectrogram(np.transpose(predicted_spectr.asnumpy()))
    audio_process.save_wave(checkpoints_dir+"/"+prefix+"_checkpoint2_"+str(i),y,hp.sr)
#

