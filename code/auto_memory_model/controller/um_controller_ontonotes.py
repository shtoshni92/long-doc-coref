import torch
import torch.nn as nn

from auto_memory_model.memory.um_memory_ontonotes import UnboundedMemoryOntoNotes
from auto_memory_model.controller.base_controller import BaseController
from auto_memory_model.utils import get_mention_to_cluster, get_ordered_mentions


class UnboundedMemControllerOntoNotes(BaseController):
    def __init__(self, new_ent_wt=1.0, **kwargs):
        super(UnboundedMemControllerOntoNotes, self).__init__(**kwargs)
        self.new_ent_wt = new_ent_wt

        self.memory_net = UnboundedMemoryOntoNotes(
            hsize=self.ment_emb_to_size_factor[self.ment_emb] * self.hsize + self.emb_size,
            drop_module=self.drop_module, **kwargs)

    @staticmethod
    def get_actions(pred_mentions, clusters):
        # Useful data structures
        mention_to_cluster = get_mention_to_cluster(clusters)

        actions = []
        cell_to_cluster = {}
        cluster_to_cell = {}

        cell_counter = 0
        for mention in pred_mentions:
            if tuple(mention) not in mention_to_cluster:
                # Not a mention
                actions.append((cell_counter, 'o'))
                cell_counter += 1
            else:
                mention_cluster = mention_to_cluster[tuple(mention)]
                if mention_cluster in cluster_to_cell:
                    # Cluster is already being tracked
                    actions.append((cluster_to_cell[mention_cluster], 'c'))
                else:
                    # Cluster is not being tracked
                    # Add the mention to being tracked
                    cluster_to_cell[mention_cluster] = cell_counter
                    cell_to_cluster[cell_counter] = mention_cluster
                    actions.append((cell_counter, 'o'))
                    cell_counter += 1

        return actions

    def calculate_coref_loss(self, action_prob_list, action_tuple_list):
        num_cells = 0
        coref_loss = 0.0

        for idx, (cell_idx, action_str) in enumerate(action_tuple_list):
            # if idx == 0:
            #     continue
            gt_idx = None
            if action_str == 'c':
                gt_idx = cell_idx
            elif action_str == 'o':
                # Overwrite
                gt_idx = (1 if num_cells == 0 else num_cells)
                num_cells += 1

            target = torch.tensor([gt_idx]).cuda()
            logit_tens = torch.unsqueeze(action_prob_list[idx], dim=0)

            # print(target, logit_tens.shape)
            weight = torch.ones_like(action_prob_list[idx]).float().cuda()
            weight[-1] = self.new_ent_wt
            coref_loss += torch.nn.functional.cross_entropy(input=logit_tens, target=target, weight=weight)

        return coref_loss

    def forward(self, example, teacher_forcing=False):
        """
        Encode a batch of excerpts.
        """
        pred_mentions, gt_actions, mention_emb_list, mention_score_list = self.get_mention_embs_and_actions(example)

        metadata = {}
        if self.dataset == 'ontonotes':
            metadata = {'genre': self.get_genre_embedding(example)}

        coref_new_prob_list, action_list = self.memory_net(
            mention_emb_list, mention_score_list, gt_actions, metadata,
            teacher_forcing=teacher_forcing)

        loss = {}

        coref_loss = 0.0
        if self.training or teacher_forcing:
            coref_loss = self.calculate_coref_loss(coref_new_prob_list, gt_actions)
            loss['coref'] = coref_loss/len(mention_emb_list)
            loss['total'] = loss['coref']
            return loss, action_list, pred_mentions, gt_actions
        else:
            return coref_loss, action_list, pred_mentions, gt_actions