'''
Diverse Client Selection For Federated Learning via Submodular Maximization

Reference:
    https://openreview.net/pdf?id=nwKXyFvaUm
'''
from .client_selection import ClientSelection
import numpy as np
import math
import torch
from tqdm import tqdm
from itertools import product



'''Diverse Client Selection'''
class DivFL(ClientSelection):
    def __init__(self, total, device, subset_ratio=0.1):
        super().__init__(total, device)
        '''
        Args:
            subset ratio: 0.1
        '''
        if subset_ratio is None:
            sys.exit('Please set the hyperparameter: subset ratio! =)')
        self.subset_ratio = subset_ratio

    def init(self, global_m, l=None):
        self.prev_global_m = global_m

    def select(self, n, client_idxs, metric, round=0, results=None):
        # pre-select
        '''
        ---
        Args
            metric: local_gradients
        '''
        # get clients' gradients
        local_grads = self.get_gradients(self.prev_global_m, metric)
        # get clients' dissimilarity matrix
        self.norm_diff = self.get_matrix_similarity_from_grads(local_grads)
        # stochastic greedy
        selected_clients = self.stochastic_greedy(len(client_idxs), n)
        return list(selected_clients)

    def get_gradients(self, global_m, local_models):
        """
        return the `representative gradient` formed by the difference
        between the local work and the sent global model
        """
        local_model_params = []
        for model in local_models:
            local_model_params += [[tens.detach().to(self.device) for tens in list(model.parameters())]] #.numpy()

        global_model_params = [tens.detach().to(self.device) for tens in list(global_m.parameters())]

        local_model_grads = []
        for local_params in local_model_params:
            local_model_grads += [[local_weights - global_weights
                                   for local_weights, global_weights in
                                   zip(local_params, global_model_params)]]

        return local_model_grads

    def get_matrix_similarity_from_grads(self, local_model_grads):
        """
        return the similarity matrix where the distance chosen to
        compare two clients is set with `distance_type`
        """
        n_clients = len(local_model_grads)
        metric_matrix = torch.zeros((n_clients, n_clients), device=self.device)
        for i, j in tqdm(product(range(n_clients), range(n_clients)), desc='>> similarity', total=n_clients**2, ncols=80):
            grad_1, grad_2 = local_model_grads[i], local_model_grads[j]
            for g_1, g_2 in zip(grad_1, grad_2):
                metric_matrix[i, j] += torch.sum(torch.square(g_1 - g_2))

        return metric_matrix

    def stochastic_greedy(self, num_total_clients, num_select_clients):
        # num_clients is the target number of selected clients each round,
        # subsample is a parameter for the stochastic greedy alg
        # initialize the ground set and the selected set
        V_set = set(range(num_total_clients))
        SUi = set()

        m = max(num_select_clients, int(self.subset_ratio * num_total_clients))
        for ni in range(num_select_clients):
            if m < len(V_set):
                R_set = np.random.choice(list(V_set), m, replace=False)
            else:
                R_set = list(V_set)
            if ni == 0:
                marg_util = self.norm_diff[:, R_set].sum(0)
                i = marg_util.argmin()
                client_min = self.norm_diff[:, R_set[i]]
            else:
                client_min_R = torch.minimum(client_min[:, None], self.norm_diff[:, R_set])
                marg_util = client_min_R.sum(0)
                i = marg_util.argmin()
                client_min = client_min_R[:, i]
            SUi.add(R_set[i])
            V_set.remove(R_set[i])
        return SUi

class Proj_Bandit(ClientSelection):
    def __init__(self, total, device):
        super().__init__(total, device)

        self.client2rewards = []
        for _ in range(total):
            self.client2rewards.append([0])
        self.client2proj = np.array([-math.inf] * total)

        self.client2selected_cnt = np.array([0] * total)
        self.client_update_cnt = 0
        self.total = total

        self.global_accu = 0
        self.global_loss = 1e6
    
    def setup(self, n_samples):
        self.accuracy_per_update = [self.global_accu]
        self.loss_per_update = [self.global_loss]

    def init(self, global_m, l=None):
        self.prev_global_m = global_m

    def update_proj_list(self, selected_client_idxs, global_m, prev_global_m, local_models, improved):
        """
        return the `projected gradient` 
        """

        local_model_params = []
        for model in local_models:
            local_model_params.append([tens.detach().to(self.device) for tens in list(model.parameters())]) #.numpy()
        
        prev_global_model_params = [tens.detach().to(self.device) for tens in list(prev_global_m.parameters())]
        global_model_params = [tens.detach().to(self.device) for tens in list(global_m.parameters())]
        global_grad = [(global_weights - prev_global_weights).flatten()
                                   for global_weights, prev_global_weights in
                                   zip(global_model_params, prev_global_model_params)]

        local_model_grads = []
        for local_params in local_model_params:
            local_model_grads.append([(local_weights - prev_global_weights).flatten()
                                   for local_weights, prev_global_weights in
                                   zip(local_params, prev_global_model_params)])

        idxs_proj = []
        # import pdb; pdb.set_trace()
        grad_norm = [torch.sqrt(torch.sum(global_grad_per_key**2)) for global_grad_per_key in global_grad]
        for local_grad in local_model_grads:
            proj_list = []
            for local_grad_per_key, global_grad_per_key, grad_norm_per_key in zip(local_grad, global_grad, grad_norm):
                proj_list.append(torch.dot(local_grad_per_key, global_grad_per_key) / grad_norm_per_key)
            idxs_proj.append(torch.mean(torch.Tensor(proj_list)))

        final_reward = torch.nn.Softmax(dim=0)(torch.Tensor(idxs_proj)) * improved
        # print("projection after softmax", final_reward)
        for client_idx, reward in zip(selected_client_idxs, (final_reward)):
            self.client2rewards[client_idx].append((reward))

        for client_idx in range(len(self.client2proj)):
            self.client2proj[client_idx] = np.mean(self.client2rewards[client_idx])
    
    def get_ucb(self):
        momemtum_based_grad_proj = self.client2proj
        # print("Proj", momemtum_based_grad_proj)
        assert isinstance(self.client2proj, list) or isinstance(self.client2proj, np.ndarray)
        # assert len(self.client2proj) == num_of_client

        alpha = 0.1
        ucb = self.client2proj + alpha * np.sqrt((2 * np.log(self.client_update_cnt))/self.client2selected_cnt)
        # print("ucb", ucb)
        return ucb
    
    def post_update(self, client_idxs, local_models, global_m):
        # import pdb; pdb.set_trace()
        self.accuracy_per_update.append(self.global_accu)
        self.loss_per_update.append(self.global_loss)

        if self.accuracy_per_update[-1] > self.accuracy_per_update[-2]:
            ### Better accuracy, larger projection is better
            improved = 1
        elif self.accuracy_per_update[-1] == self.accuracy_per_update[-2]:
            if self.loss_per_update[-1] < self.loss_per_update[-2]:
                improved = 0.5
            elif self.loss_per_update[-1] > self.loss_per_update[-2]:
                improved = -0.5
            else: 
                improved = 0
        else:
            ### Worse accuracy, smaller projection is better
            improved = -1

        self.update_proj_list(client_idxs, global_m, self.prev_global_m, local_models, improved)

    def select(self, n, client_idxs, metric, round=0, results=None):
        # pre-select
        '''
        ---
        Args
            metric: local_gradients
        '''
        # get clients' projected gradients
        # import pdb; pdb.set_trace()
        if self.client_update_cnt == 0:
            # selected_client_index = np.arange(self.total)
            selected_client_index = np.random.choice(self.total, n, replace=False)
        else:
            ucb = self.get_ucb()
            sorted_client_idxs = ucb.argsort()[::-1]
            ### Select clients
            selected_client_index = sorted_client_idxs[:n]
            for client_idx in selected_client_index:
                self.client2selected_cnt[client_idx] += 1

        self.client_update_cnt += 1

        return selected_client_index.astype(int)

         
