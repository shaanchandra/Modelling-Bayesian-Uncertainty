import argparse, time, datetime, shutil
import pprint
import sys, os, glob
import argparse
import warnings
warnings.filterwarnings("ignore")

import numpy as np

import torch
import torchtext
#from torch.utils.tensorboard import SummaryWriter
from tensorboardX import SummaryWriter
from nltk import word_tokenize
import nltk
nltk.download('punkt')
from torch.autograd import Variable
from torchtext.data import Field, BucketIterator
from torchtext import datasets
import torch.nn as nn
from sklearn.metrics import accuracy_score

from data_utils import *
from models import *
from utils import *



def eval_network(model, test = False):
    eval_precision, eval_recall, eval_f1, eval_accuracy = [],[],[], []
    total_iters = int(np.ceil(len(val_y)/config['batch_size'])) if not test else int(np.ceil(len(test_y)/config['batch_size']))
    with torch.no_grad():
        for iters in range(total_iters):
            start_idx = iters*config['batch_size']
            end_idx = start_idx + config['batch_size']
    
            doc_batch, doc_lens = get_batch_from_idx(config, embeddings, val_x[start_idx : end_idx]) if not test else get_batch_from_idx(config, embeddings, test_x[start_idx : end_idx])
            doc_batch = Variable(doc_batch)
            label_batch = Variable(torch.FloatTensor(val_y[start_idx:end_idx])).to(device) if not test else Variable(torch.FloatTensor(test_y[start_idx:end_idx])).to(device)
            preds = model(doc_batch.to(device), doc_lens.to(device))
            preds = (preds>0.5).type(torch.FloatTensor)
            f1, recall, precision, accuracy = evaluation_measures(config, preds, label_batch)
            eval_f1.append(f1)
            eval_precision.append(precision)
            eval_recall.append(recall)
            eval_accuracy.append(accuracy)
        eval_precision = sum(eval_precision)/len(eval_precision)
        eval_recall = sum(eval_recall)/len(eval_recall)
        eval_f1 = sum(eval_f1)/len(eval_f1)
        eval_accuracy = sum(eval_accuracy)/len(eval_accuracy)
    return eval_f1, eval_precision, eval_recall, eval_accuracy



def train_network():
    print("\n\n"+ "="*80 + "\n\t\t\t\t Training Network\n" + "="*80)

    # Seeds for reproduceable runs
    torch.manual_seed(42)
    torch.cuda.manual_seed(42)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    # Initialize the model, optimizer and loss function
    model = Doc_Classifier(config).to(device)
    if config['optimizer'] == 'Adam':
        optimizer = torch.optim.Adam(model.parameters(), lr = config['lr'], weight_decay = config['weight_decay'])
    elif config['optimizer'] == 'SGD':
        optimizer = torch.optim.SGD(model.parameters(), lr = config['lr'], momentum = config['momentum'], weight_decay = config['weight_decay'])
    criterion = nn.BCELoss(reduction = 'mean')
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer,step_size= config["lr_decay_step"], gamma= config["lr_decay_factor"])

    # Load the checkpoint to resume training if found
    model_file = os.path.join(config['model_checkpoint_path'], config['model_name'], config['model_save_name'])
    if os.path.isfile(model_file):
        checkpoint = torch.load(model_file)
        model.load_state_dict(checkpoint['model_state_dict'])
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        start_epoch = checkpoint['epoch'] + 1
        print("\nResuming training from epoch {} with loaded model and optimizer...\n".format(start_epoch))
        print("Using the model defined below: \n\n")
        print(model)
    else:
        start_epoch = 1
        print("\nNo Checkpoints found for the chosen model to reusme training... \nTraining the  ''{}''  model from scratch...\n".format(config['model_name']))
        print("Using the model defined below: \n\n")
        print(model)

    start = time.time()
    best_val_f1 = 0
    prev_val_f1 = 0
    total_iters = 0
    train_loss = []
    train_f1_score, train_recall_score, train_precision_score, train_accuracy_score = [], [], [], []
    terminate_training = False
    print("\nBeginning training at:  {} \n".format(datetime.datetime.now()))
    for epoch in range(start_epoch, config['max_epoch']+1):
        model.train()

        # Shuffling data for batching in each epoch
        permute_idxs = np.random.permutation(len(train_y))
        train_data = [train_x[i] for i in permute_idxs]
        train_labels = [train_y[i] for i in permute_idxs]

        #train_data = train_x[:1000]
        #train_labels = train_y[:1000]

        max_iters = int(np.ceil(len(train_labels)/config['batch_size']))
        for iters in range(max_iters):
            model.train()
            # lr_scheduler.step()

            start_idx = iters*config['batch_size']
            end_idx = start_idx + config['batch_size']

            doc_batch, doc_lens = get_batch_from_idx(config, embeddings, train_data[start_idx : end_idx])
            doc_batch = Variable(doc_batch)
            label_batch = Variable(torch.FloatTensor(train_labels[start_idx:end_idx])).to(device)

            preds = model(doc_batch.to(device), doc_lens.to(device))
            loss = criterion(preds, label_batch)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 10)
            optimizer.step()

            preds = (preds>0.5).type(torch.FloatTensor)
            train_f1, train_recall, train_precision, train_accuracy = evaluation_measures(config, preds, label_batch)
            train_f1_score.append(train_f1)
            train_accuracy_score.append(train_accuracy)
            train_recall_score.append(train_recall)
            train_precision_score.append(train_precision)
            train_loss.append(loss.detach().item())
            if iters%100 == 0:
                writer.add_scalar('Train/loss', sum(train_loss)/len(train_loss), ((iters+1)+ total_iters))
                writer.add_scalar('Train/precision', sum(train_precision_score)/len(train_precision_score), ((iters+1)+total_iters))
                writer.add_scalar('Train/recall', sum(train_recall_score)/len(train_recall_score), ((iters+1)+total_iters))
                writer.add_scalar('Train/f1', sum(train_f1_score)/len(train_f1_score), ((iters+1)+total_iters))
                writer.add_scalar('Train/accuracy', sum(train_accuracy_score)/len(train_accuracy_score), ((iters+1)+total_iters))

                for name, param in model.named_parameters():
                    if not param.requires_grad:
                        continue
                    writer.add_histogram('iters/'+name, param.data.view(-1), global_step= ((iters+1)+total_iters))
                    writer.add_histogram('grads/'+ name, param.grad.data.view(-1), global_step = ((iters+1)+ total_iters))

        total_iters += iters

        # Evaluate on test set
        eval_f1, eval_precision, eval_recall, eval_accuracy = eval_network(model)

        # print stats
        print_stats(config, epoch, sum(train_accuracy_score)/len(train_accuracy_score), sum(train_loss)/len(train_loss), sum(train_f1_score)/len(train_f1_score), eval_accuracy, eval_f1, start)

        writer.add_scalar('Validation/f1', eval_f1, epoch)
        writer.add_scalar('Validation/recall', eval_recall, epoch)
        writer.add_scalar('Validation/precision', eval_precision, epoch)
        writer.add_scalar('Validation/accuracy', eval_accuracy, epoch)

        for name, param in model.named_parameters():
            if not param.requires_grad:
                continue
            writer.add_histogram('epochs/' + name, param.data.view(-1), global_step= epoch)

        # Save model checkpoints for best model
        if eval_f1 > best_val_f1:
            print("New High Score! Saving model...\n")
            best_val_f1 = eval_f1
            # Save the state and the vocabulary
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'text_vocab': vocab,
            }, os.path.join(config['model_checkpoint_path'], config['model_name'], config['model_save_name']))

        # If validation accuracy does not improve, divide the learning rate by 5 and
        # if learning rate falls below 1e-5 terminate training
        if eval_f1 <= prev_val_f1:
            for param_group in optimizer.param_groups:
                if param_group['lr'] < config['lr_cut_off']:
                    terminate_training = True
                    break
                param_group['lr'] /= 5
                print("Learning rate changed to :  {}\n".format(param_group['lr']))

        prev_val_f1 = eval_f1
        if terminate_training:
            break

    # Termination message
    if terminate_training:
        print("\n" + "-"*100 + "\nTraining terminated because the learning rate fell below:  {}" .format(config['lr_cut_off']))
    else:
        print("\n" + "-"*100 + "\nMaximum epochs reached. Finished training !!")

    print("\n" + "-"*50 + "\n\t\tEvaluating on test set\n" + "-"*50)
    model_file = os.path.join(config['model_checkpoint_path'], config['model_name'], config['model_save_name'])
    if os.path.isfile(model_file):
        checkpoint = torch.load(model_file)
        model.load_state_dict(checkpoint['model_state_dict'])
    else:
        raise ValueError("No Saved model state_dict found for the chosen model...!!! \nAborting evaluation on test set...".format(config['model_name']))
    test_f1, test_precision, test_recall, test_accuracy = eval_network(model, test = True)
    print("\nTest precision of best model = {:.2f}".format(test_precision*100))
    print("\nTest recall of best model = {:.2f}".format(test_recall*100))
    print("\nTest f1 of best model = {:.2f}".format(test_f1*100))
    print("\nTest accuracy of best model = {:.2f}".format(test_accuracy*100))

    writer.close()
    return None




if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    # Required Paths
    parser.add_argument('--reuters_path', type = str, default = '../data/reuters',
                          help='path to reuters data (raw data)')
    parser.add_argument('--glove_path', type = str, default = '../data/glove/glove.840B.300d.txt',
                          help='path for Glove embeddings (850B, 300D)')
    parser.add_argument('--model_checkpoint_path', type = str, default = './model_checkpoints',
                          help='Directory for saving trained model checkpoints')
    parser.add_argument('--vis_path', type = str, default = './vis_checkpoints',
                          help='Directory for saving tensorboard checkpoints')
    parser.add_argument("--model_save_name", type=str, default= 'best_model.pt',
                       help = 'saved model name')

    # Training Params
    parser.add_argument('--model_name', type = str, default = 'bilstm',
                          help='model name: bilstm / bilstm_pool / bilstm_attn')
    parser.add_argument('--lr', type = float, default = 0.01,
                          help='Learning rate for training')
    parser.add_argument('--batch_size', type = int, default = 32,
                          help='batch size for training"')
    parser.add_argument('--embed_dim', type = int, default = 300,
                          help='dimension of word embeddings used(GLove)"')
    parser.add_argument('--lstm_dim', type = int, default = 512,
                          help='dimen of hidden unit of LSTM/BiLSTM networks"')
    parser.add_argument('--fc_dim', type = int, default = 256,
                          help='dimen of FC layer"')
    parser.add_argument('--n_classes', type = int, default = 90,
                          help='number of classes"')
    parser.add_argument('--optimizer', type = str, default = 'Adam',
                        help = 'Optimizer to use for training')
    parser.add_argument('--embed_dpout', type = float, default = 0.3,
                        help = 'Embedding Dropout for training')
    parser.add_argument('--weight_dpout', type = float, default = 0.3,
                        help = 'Network weight Dropout for training')
    parser.add_argument('--weight_decay', type = float, default = 1e-4,
                        help = 'weight decay for optimizer')
    parser.add_argument('--momentum', type = float, default = 0.8,
                        help = 'Momentum for optimizer')
    parser.add_argument('--max_epoch', type = int, default = 50,
                        help = 'Max epochs to train for')
    parser.add_argument('--val_split', type = int, default = 0.1,
                        help = 'Ratio of training data to be split into validation set')
    parser.add_argument('--lr_decay_step', type = float, default = 2000,
                        help = 'Number of steps after which learning rate should be decreased')
    parser.add_argument('--lr_decay_factor', type = float, default = 0.2,
                        help = 'Decay of learning rate of the optimizer')
    parser.add_argument('--lr_cut_off', type = float, default = 1e-7,
                        help = 'Lr lower bound to stop training')

    args, unparsed = parser.parse_known_args()
    config = args.__dict__

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    config['device'] = device

    # global dtype
    # dtype = torch.FloatTensor

    classes = ['acq', 'alum', 'barley', 'bop', 'carcass', 'castor-oil', 'cocoa', 'coconut', 'coconut-oil', 'coffee', 'copper', 'copra-cake', 'corn', 'cotton', 'cotton-oil',
               'cpi', 'cpu', 'crude', 'dfl', 'dlr', 'dmk', 'earn', 'fuel', 'gas', 'gnp', 'gold', 'grain', 'groundnut', 'groundnut-oil', 'heat', 'hog', 'housing', 'income',
               'instal-debt', 'interest', 'ipi', 'iron-steel', 'jet', 'jobs', 'l-cattle', 'lead', 'lei', 'lin-oil', 'livestock', 'lumber', 'meal-feed', 'money-fx',
               'money-supply', 'naphtha', 'nat-gas', 'nickel', 'nkr', 'nzdlr', 'oat', 'oilseed', 'orange', 'palladium', 'palm-oil', 'palmkernel', 'pet-chem', 'platinum',
               'potato', 'propane', 'rand', 'rape-oil', 'rapeseed', 'reserves', 'retail', 'rice', 'rubber', 'rye', 'ship', 'silver', 'sorghum', 'soy-meal', 'soy-oil', 'soybean',
               'strategic-metal', 'sugar', 'sun-meal', 'sun-oil', 'sunseed', 'tea', 'tin', 'trade', 'veg-oil', 'wheat', 'wpi', 'yen', 'zinc']

    # Check all provided paths:
    model_path = os.path.join(config['model_checkpoint_path'], config['model_name'])
    vis_path = os.path.join(config['vis_path'], config['model_name'])
    if not os.path.exists(config['reuters_path']):
        raise ValueError("[!] ERROR: Reuters data path does not exist")
    else:
        print("\nReuters Data path checked..")
    if not os.path.exists(config['glove_path']):
        raise ValueError("[!] ERROR: Glove Embeddings path does not exist")
    else:
        print("\nGLOVE embeddings path checked..")
    if not os.path.exists(model_path):
        print("\nCreating checkpoint path for saved models at:  {}\n".format(model_path))
        os.makedirs(model_path)
    else:
        print("\nModel save path checked..")
    if config['model_name'] not in ['bilstm', 'bilstm_pool', 'bilstm_attn']:
        raise ValueError("[!] ERROR:  model_name is incorrect. Choose one of - bilstm / bilstm_pool / bilstm_attn")
    else:
        print("\nModel name checked...")
    if not os.path.exists(vis_path):
        print("\nCreating checkpoint path for Tensorboard visualizations at:  {}\n".format(vis_path))
        os.makedirs(vis_path)
    else:
        print("\nTensorbaord Visualization path checked..")
        print("Cleaning Visualization path of older tensorboard files...\n")
        shutil.rmtree(vis_path)


    # Prepare the tensorboard writer
    writer = SummaryWriter(os.path.join(args.vis_path, config['model_name']))

    # Prepare the datasets and iterator for training and evaluation
    train_x, train_y, val_x, val_y, test_x, test_y, vocab, embeddings = prepare_training(config, classes)

    #Print args
    print("\n" + "x"*50 + "\n\nRunning training with the following parameters: \n")
    for key, value in config.items():
        print(key + ' : ' + str(value))
    print("\n" + "x"*50)

    train_network()