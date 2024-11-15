from typing import Optional, Tuple, Literal

import torch
from pytorch3d.ops import ball_query, knn_gather, knn_points, sample_farthest_points
from pytorch3d.ops.utils import masked_gather
from torch import nn

from pointjepa.modules.Pointnet import PointNetEncoder


def fill_empty_indices(idx: torch.Tensor) -> torch.Tensor:
    """
    replaces all empty indices (-1) with the first index from its group
    """
    B, G, K = idx.shape

    mask = idx == -1
    first_idx = idx[:, :, 0].unsqueeze(-1).expand(-1, -1, K)
    idx[mask] = first_idx[mask]  # replace -1 index with first index
    # print(f"DEBUG: {(len(idx[mask].view(-1)) / len(idx.view(-1))) * 100:.1f}% of ball query indices are empty")

    return idx


class PointcloudGrouping(nn.Module):
    def __init__(
        self,
        num_groups: int,
        group_size: int,
        group_radius: float | None,
    ):
        super().__init__()
        self.num_groups = num_groups
        self.group_size = group_size
        self.group_radius = group_radius

    def forward(self, points: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        # points: (B, N, C)
        group_centers, _ = sample_farthest_points(
            points[:, :, :3].float(), K=self.num_groups, random_start_point=True
        )

        if self.group_radius is None:
            _, idx, _ = knn_points(
                group_centers.float(),
                points[:, :, :3].float(),
                K=self.group_size,
                return_sorted=False,
                return_nn=False,
            )  # (B, G, K)
            groups = knn_gather(points, idx)  # (B, G, K, C)
        else:
            _, idx, _ = ball_query(
                group_centers.float(),
                points[:, :, :3].float(),
                K=self.group_size,
                radius=self.group_radius,
                return_nn=False,
            )  # (B, G, K)
            groups = masked_gather(points, fill_empty_indices(idx))  # (B, G, K, C)

        groups[:, :, :, :3] = groups[:, :, :, :3] - group_centers.unsqueeze(2)
        if self.group_radius is not None:
            groups = (
                groups / self.group_radius
            )  # proposed by PointNeXT to make relative coordinates less small
        return groups, group_centers  # (B, G, K, C), (B, G, 3)


class PointcloudTokenizer(nn.Module):
    def __init__(
        self,
        num_groups: int,
        group_size: int,
        group_radius: float | None,
        token_dim: int
    ) -> None:
        super().__init__()
        self.token_dim = token_dim
        self.grouping = PointcloudGrouping(
            num_groups=num_groups,
            group_size=group_size,
            group_radius=group_radius,
        )

        self.embedding = PointNetEncoder(3, token_dim, "small")
          
    def forward(self, points: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        # points: (B, N, 3)
        group: torch.Tensor
        group_center: torch.Tensor
        tokens: torch.Tensor

        group, group_center = self.grouping(points)  # (B, G, K, C), (B, G, 3)
        B, G, S, C = group.shape
        tokens = self.embedding(group.reshape(B * G, S, C)).reshape(
            B, G, self.token_dim
        )  # (B, G, C')
        return tokens, group_center
