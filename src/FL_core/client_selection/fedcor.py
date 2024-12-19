from .client_selection import ClientSelection
import numpy as np
import math
import torch
from copy import deepcopy
import copy
from tqdm import tqdm
from itertools import product
import sys
import signal
import atexit

from utils import logger

from fedcor.GPR import Kernel_GPR, Matrix_GPR, Poly_Kernel, SE_Kernel
from fedcor.mvnt import MVN_Test

class FedCor(ClientSelection):
    def __init__(self, args, total, device):
        super().__init__(total, device)
        
        self.args = args
        
        if args.kernel=='Poly':
            self.gpr = Kernel_GPR(self.total, loss_type=args.train_method,
                            reusable_history_length=args.group_size,
                            gamma=args.GPR_gamma,
                            device=gpr_device,
                            dimension=args.dimension,
                            kernel=Poly_Kernel,
                            order=1, Normalize=args.poly_norm)
        elif args.kernel=='SE':
            self.gpr = Kernel_GPR(self.total, loss_type=args.train_method,
                            reusable_history_length=args.group_size,
                            gamma=args.GPR_gamma,
                            device=gpr_device,
                            dimension=args.dimension,
                            kernel=SE_Kernel)
        else:
            self.gpr = Matrix_GPR(self.total, loss_type=args.train_method,
                            reusable_history_length=args.group_size,
                            gamma=args.GPR_gamma,
                            device=gpr_device)
        self.gpr.to(gpr_device)
        
        self.chosen_clients = []     # chosen clients on each epoch
        self.gt_global_losses = []   # test losses on global models(after averaging) over all clients
        
        init_mu = args.mu

        self.sigma = []
        self.sigma_gt = []
        
        def signal_handler(*_args):
            print()
            select_cnt = np.array([0] * total)
            for idx in np.array(chosen_clients).flatten():
                select_cnt[idx] += 1
            print(select_cnt)
            sys.exit(0)
        # signal.signal(signal.SIGINT, signal_handler)
        # signal.signal(signal.SIGTERM, signal_handler)
        atexit.register(signal_handler)
        
        # Test the global model before training
        list_acc, list_loss = federated_test_idx(args, global_model,
                                                list(range(self.total)),
                                                train_dataset,user_groups)
        self.gt_global_losses.append(list_loss)
        
        self.warmup = xxx
        self.gpr_begin = xxx
        self.update_mean = xxx
        self.verbose = xxx
        self.GPR_interval = xxx
        self.epsilon_greedy = xxx
        self.dynamic_C = xxx
        self.dynamic_TH = xxx
        self.discount = xxx
        self.mvnt = xxx
        self.mvnt_interval = xxx
        
    def init(self, global_m, l=None):
        epoch_global_losses = []
        epoch_local_losses = []
        
    def select(self, selected_num, epoch, weights):
        # Client Selection
        if epoch > self.warmup:
            # FedCor
            idxs_users = self.gpr.Select_Clients(selected_num, self.epsilon_greedy,
                                            weights, self.dynamic_C,
                                            self.dynamic_TH)
            print("GPR Chosen Clients:", idxs_users)
        else:
            # Random selection
            idxs_users = np.random.choice(
                range(self.total), selected_num, replace=False)
            
        self.chosen_clients.append(idxs_users)
        
        return idxs_users
    
    def train_gpr(self, epoch, global_model):
        
        if epoch >= self.gpr_begin:
            if epoch <= self.warmup:    # warm-up
                self.gpr.Update_Training_Data([np.arange(self.total),], 
                                              [np.array(self.gt_global_losses[-1]) - np.array(self.gt_global_losses[-2]),],
                                              epoch=epoch)
                if not self.update_mean:
                    print("Training GPR")
                    self.gpr.Train(lr=1e-2, llr=0.01, max_epoches=150, schedule_lr=False,
                                   update_mean=self.update_mean, verbose=self.verbose)
                elif epoch == self.warmup:
                    print("Training GPR")
                    self.gpr.Train(lr=1e-2, llr=0.01, max_epoches=1000, schedule_lr=False,
                                   update_mean=self.update_mean, verbose=self.verbose)

            elif epoch > self.warmup and epoch % self.GPR_interval == 0:# normal and optimization round
                self.gpr.Reset_Discount()
                print("Training with Random Selection For GPR Training:")
                random_idxs_users = np.random.choice(range(self.total), selected_num, replace=False)
                ### TODO
                gpr_acc, gpr_loss = train_federated_learning(self.args, epoch, copy.deepcopy(global_model), 
                                                            random_idxs_users, train_dataset, user_groups)
                self.gpr.Update_Training_Data([np.arange(self.total),], 
                                              [np.array(gpr_loss)-np.array(self.gt_global_losses[-1]),],
                                              epoch=epoch)
                print("Training GPR")
                self.gpr.Train(lr=1e-2, llr=0.01, max_epoches=self.GPR_Epoch, schedule_lr=False, 
                               update_mean=self.update_mean, verbose=self.verbose)

            else:# normal and not optimization round
                self.gpr.Update_Discount(idxs_users, self.discount)
            
        if self.mvnt and (epoch == self.warmup or (epoch % self.mvnt_interval == 0 and epoch > self.warmup)):
            mvn_file = file_name + '/MVN/{}'.format(seed)
            if not os.path.exists(mvn_file):
                os.makedirs(mvn_file)
            mvn_samples = MVN_Test(self.args, copy.deepcopy(global_model), train_dataset, user_groups,
                                        file_name+'/MVN/{}/{}.csv'.format(seed, epoch))
            self.sigma_gt.append(np.cov(mvn_samples, rowvar=False, bias=True))
            self.sigma.append(gpr.Covariance().clone().detach().numpy())
            
    def test_gpr(self, epoch):
        # test prediction accuracy of GP model
        if epoch > self.warmup:
            test_idx = np.random.choice(range(self.total), selected_num, replace=False)
            test_data = np.concatenate([np.expand_dims(list(range(self.total)), 1),
                                        np.expand_dims(np.array(self.gt_global_losses[-1]) - np.array(self.gt_global_losses[-2]), 1),
                                        np.ones([self.total,1])], 1)
            pred_idx = np.delete(list(range(self.total)), test_idx)
            
            try:
                predict_loss, mu_p, sigma_p = self.gpr.Predict_Loss(test_data, test_idx, pred_idx)
                print("GPR Predict relative Loss:{:.4f}".format(predict_loss))
            except:
                print("[Warning]: Singular posterior covariance encountered, skip the GPR test in this round!")

