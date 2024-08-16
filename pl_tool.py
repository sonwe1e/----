import torch
import lightning.pytorch as pl
import torch.nn.functional as F
import numpy as np
from sklearn.metrics import (
    confusion_matrix,
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
)

torch.set_float32_matmul_precision("high")


class LightningModule(pl.LightningModule):
    def __init__(self, opt, model, len_trainloader):
        super().__init__()
        self.learning_rate = opt.learning_rate
        self.len_trainloader = len_trainloader
        self.opt = opt
        self.model = model
        self.ce_loss = torch.nn.CrossEntropyLoss(label_smoothing=0.1)
        self.train_predictions = []
        self.val_predictions = []
        self.train_labels = []
        self.val_labels = []

    def forward(self, x):
        pred = self.model(x)
        return pred

    def configure_optimizers(self):
        self.optimizer = torch.optim.AdamW(
            self.parameters(),
            weight_decay=self.opt.weight_decay,
            betas=(0.9, 0.95),
            lr=self.learning_rate,
        )
        self.scheduler = torch.optim.lr_scheduler.OneCycleLR(
            self.optimizer,
            max_lr=self.learning_rate,
            epochs=self.opt.epochs,
            pct_start=0.06,
            steps_per_epoch=self.len_trainloader,
            anneal_strategy="linear",
        )
        return {
            "optimizer": self.optimizer,
            "lr_scheduler": {
                "scheduler": self.scheduler,
                "interval": "step",
            },
        }

    def training_step(self, batch, batch_idx):
        image, label = batch["image"], batch["label"]
        # self.log_image("input", batch["image"])
        rate = np.random.random()
        if rate < self.opt.aug_line[0]:
            image, label_a, label_b, lam = self.mixup(
                image,
                label,
            )
        elif self.opt.aug_line[0] < rate < self.opt.aug_line[1]:
            image, label_a, label_b, lam = self.cutmix(
                image,
                label,
            )
        elif self.opt.aug_line[1] < rate < self.opt.aug_line[2]:
            image = self.cutout(image, length=64)
            label_a, label_b, lam = label, label, 1.0
        else:
            label_a, label_b, lam = label, label, 1.0

        prediction = self(image)
        ce_loss = self.ce_loss(prediction, label_a) * lam + self.ce_loss(
            prediction, label_b
        ) * (1 - lam)
        prediction = torch.argmax(prediction, dim=1)
        # prediction = torch.round(torch.sigmoid(prediction))
        self.train_predictions.append(prediction)
        self.train_labels.append(label)
        loss = ce_loss
        self.log("train_ce_loss", ce_loss)
        self.log("train_loss", loss)
        self.log("learning_rate", self.scheduler.get_last_lr()[0])
        return loss

    def validation_step(self, batch, batch_idx):
        image, label = batch["image"], batch["label"]
        # self.log_image("input", batch["image"])
        prediction = self(image)
        ce_loss = self.ce_loss(prediction, label)
        prediction = torch.argmax(prediction, dim=1)
        # prediction = torch.round(torch.sigmoid(prediction))
        self.val_predictions.append(prediction)
        self.val_labels.append(label)
        loss = ce_loss
        self.log("valid_ce_loss", ce_loss)
        self.log("valid_loss", loss)

    def on_train_epoch_end(self):
        self.train_predictions = torch.cat(self.train_predictions)
        self.train_labels = torch.cat(self.train_labels)
        self.train_predictions = self.train_predictions.cpu().numpy()
        self.train_labels = self.train_labels.cpu().numpy()

        # 计算混淆矩阵
        cm = confusion_matrix(self.train_labels, self.train_predictions)

        # 计算指标
        acc = accuracy_score(self.train_labels, self.train_predictions)
        # 计算宏平均
        prec = precision_score(
            self.train_labels, self.train_predictions, average="macro"
        )
        sens = recall_score(self.train_labels, self.train_predictions, average="macro")
        f1 = f1_score(self.train_labels, self.train_predictions, average="macro")

        self.log("train_acc", acc)
        self.log("train_precision", prec)
        self.log("train_recall", sens)
        self.log("train_f1", f1)

        self.train_labels = []
        self.train_predictions = []

    def on_validation_epoch_end(self):
        self.val_predictions = torch.cat(self.val_predictions)
        self.val_labels = torch.cat(self.val_labels)
        self.val_predictions = self.val_predictions.cpu().numpy()
        self.val_labels = self.val_labels.cpu().numpy()

        # 计算混淆矩阵
        cm = confusion_matrix(self.val_labels, self.val_predictions)

        # 计算指标
        acc = accuracy_score(self.val_labels, self.val_predictions)
        # 计算宏平均
        prec = precision_score(self.val_labels, self.val_predictions, average="macro")
        sens = recall_score(self.val_labels, self.val_predictions, average="macro")
        f1 = f1_score(self.val_labels, self.val_predictions, average="macro")

        self.log("valid_acc", acc)
        self.log("valid_precision", prec)
        self.log("valid_recall", sens)
        self.log("valid_f1", f1)

        self.val_labels = []
        self.val_predictions = []

    def mixup(self, x, y, alpha=1.0):
        if alpha > 0:
            lam = np.random.beta(alpha, alpha)
        else:
            lam = 1
        batch_size = x.size()[0]
        index = torch.randperm(batch_size)
        mixed_x = lam * x + (1 - lam) * x[index, :]
        y_a, y_b = y, y[index]
        return mixed_x, y_a, y_b, lam

    def cutmix(self, x, y, alpha=1.0):
        if alpha > 0:
            lam = np.random.beta(alpha, alpha)
        else:
            lam = 1

        batch_size = x.size()[0]
        index = torch.randperm(batch_size)

        bbx1, bby1, bbx2, bby2 = self.rand_bbox(x.size(), lam)
        x[:, :, bbx1:bbx2, bby1:bby2] = x[index, :, bbx1:bbx2, bby1:bby2]

        lam = 1 - ((bbx2 - bbx1) * (bby2 - bby1) / (x.size()[-1] * x.size()[-2]))
        y_a, y_b = y, y[index]
        return x, y_a, y_b, lam

    def rand_bbox(self, size, lam):
        W = size[2]
        H = size[3]
        cut_rat = np.sqrt(1.0 - lam)
        cut_w = (W * cut_rat).astype(np.uint16)
        cut_h = (H * cut_rat).astype(np.uint16)

        cx = np.random.randint(W)
        cy = np.random.randint(H)

        bbx1 = np.clip(cx - cut_w // 2, 0, W)
        bby1 = np.clip(cy - cut_h // 2, 0, H)
        bbx2 = np.clip(cx + cut_w // 2, 0, W)
        bby2 = np.clip(cy + cut_h // 2, 0, H)

        return bbx1, bby1, bbx2, bby2

    def cutout(self, image, length=64):
        h, w = image.size(2), image.size(3)

        x_length = torch.randint(low=int(length / 5), high=length, size=(1,)).item()
        y_length = torch.randint(low=int(length / 5), high=length, size=(1,)).item()
        y = torch.randint(high=h, size=(1,)).item()
        x = torch.randint(high=w, size=(1,)).item()

        y1 = np.clip(y - y_length // 2, 0, h)
        y2 = np.clip(y + y_length // 2, 0, h)
        x1 = np.clip(x - x_length // 2, 0, w)
        x2 = np.clip(x + x_length // 2, 0, w)

        mask = torch.ones(image.size(), device=image.device)
        if np.random.random() > 0.5:
            mask[:, :, y1:y2, x1:x2] = 0
        else:
            mask[:, :, y1:y2, x1:x2] = torch.randn_like(mask[:, :, y1:y2, x1:x2])
        masked_x = image * mask
        return masked_x
