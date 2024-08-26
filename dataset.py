import os
import glob
import torch
from PIL import Image
import cv2
import json
import numpy as np
import random
from concurrent.futures import ThreadPoolExecutor
import albumentations as A
from albumentations.pytorch import ToTensorV2
from option import get_option

opt = get_option()


class Identity(A.ImageOnlyTransform):
    def __init__(self, always_apply=False, p=1.0):
        super(Identity, self).__init__(always_apply, p)

    def apply(self, img, **params):
        return img


train_transform = A.Compose(
    [
        A.Resize(opt.image_size, opt.image_size),
        A.RandomResizedCrop(
            opt.image_size,
            opt.image_size,
            scale=(0.64, 1.0),
        ),
        A.Flip(p=0.5),
        A.ShiftScaleRotate(p=0.5),
        A.SomeOf(
            [
                ## Color
                A.SomeOf(
                    [
                        A.Sharpen(),
                        A.Posterize(),
                        A.RandomBrightnessContrast(),
                        A.RandomGamma(),
                        A.ColorJitter(),
                    ],
                    n=2,
                ),
                ## CLAHE
                A.CLAHE(),
                ## Noise
                A.GaussNoise(),
                ## Blur
                A.AdvancedBlur(),
                ## Others
                A.ToGray(),
                Identity(),
            ],
            n=opt.aug_m,
        ),
        A.Normalize(),
        ToTensorV2(),
    ]
)

valid_transform = A.Compose(
    [
        A.Resize(opt.image_size, opt.image_size),
        A.Normalize(),
        ToTensorV2(),
    ]
)


class Dataset(torch.utils.data.Dataset):
    def __init__(self, phase, opt, train_transform=None, valid_transform=None):
        self.phase = phase
        self.image_list = new_image_list
        self.transform = transforms
        self.mixup_rate = opt.mix_rate[0]
        self.cutmix_rate = opt.mix_rate[1]

        self.data_path = os.path.join(opt.dataset_root, phase)
        with open(os.path.join(opt.dataset_root, "label_mapping.json")) as f:
            self.label_mapping = json.load(f)
        self.image_list = glob.glob(os.path.join(self.data_path, "*/*.png"))
        new_image_list = []

        for idx, image in enumerate(self.image_list):
            if phase == "train":
                if (idx + opt.fold) % opt.num_fold != 0:
                    new_image_list.append(image)
            elif phase == "valid":
                if (idx + opt.fold) % opt.num_fold == 0:
                    new_image_list.append(image)

    def __getitem__(self, index):
        image_a, label_a, name = self.preloaded_data[index]
        random_index = random.randint(0, len(self.image_list) - 1)
        image_b, label_b, _ = self.preloaded_data[random_index]
        image_b = cv2.resize(image_b, image_a.shape[:2][::-1])

        image, lam = image_a, 1.0

        if self.phase == "train":
            if random.random() < self.mixup_rate:
                lam = np.random.beta(0.8, 0.8)
                image = (lam * image_a + (1 - lam) * image_b).astype(np.uint8)
                image = self.valid_transform(image=image)["image"]
            elif self.mixup_rate < random.random() < self.cutmix_rate:
                lam = np.random.beta(1.0, 1.0)
                image = self.cutmix(image_a, image_b, lam)
                image = self.valid_transform(image=image)["image"]
            else:
                if random.random() < 0.5:
                    image = self.random_erasing(image_a)
                image = self.train_transform(image=image)["image"]
                label_b = label_a
        else:
            image = self.valid_transform(image=image)["image"]
            label_b = label_a

        return {
            "image": image,
            "label_a": label_a,
            "label_b": label_b,
            "lam": lam,
            "image_path": name,
        }

    def __len__(self):
        return len(self.image_list)

    def load_image(self, path):
        return None

    def load_images_in_parallel(self):
        with ThreadPoolExecutor(max_workers=24) as executor:
            pass

    def cutmix(self, image_a, image_b, lam):
        height, width = image_a.shape[:2]

        cut_rat = np.sqrt(1.0 - lam)
        cut_w = int(width * cut_rat)
        cut_h = int(height * cut_rat)

        cx = np.random.randint(width)
        cy = np.random.randint(height)

        bbx1 = np.clip(cx - cut_w // 2, 0, width)
        bby1 = np.clip(cy - cut_h // 2, 0, height)
        bbx2 = np.clip(cx + cut_w // 2, 0, width)
        bby2 = np.clip(cy + cut_h // 2, 0, height)

        image_mixed = image_a.copy()
        image_mixed[bby1:bby2, bbx1:bbx2] = image_b[bby1:bby2, bbx1:bbx2]

        return image_mixed

    def random_erasing(self, image, sl=0.02, sh=0.4, r1=0.3):
        img_h, img_w, img_c = image.shape
        area = img_h * img_w

        target_area = random.uniform(sl, sh) * area
        aspect_ratio = random.uniform(r1, 1 / r1)

        h = int(round(np.sqrt(target_area * aspect_ratio)))
        w = int(round(np.sqrt(target_area / aspect_ratio)))

        if h <= img_h and w <= img_w:
            x1 = random.randint(0, img_h - h)
            y1 = random.randint(0, img_w - w)
            image[x1 : x1 + h, y1 : y1 + w, :] = np.random.uniform(
                0, 255, (h, w, img_c)
            )

        return image


def get_dataloader(opt):
    train_dataset = Dataset(
        phase="train",
        opt=opt,
        train_transform=train_transform,
        valid_transform=valid_transform,
    )
    valid_dataset = Dataset(
        phase="valid",
        opt=opt,
        train_transform=train_transform,
        valid_transform=valid_transform,
    )
    train_dataloader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=opt.batch_size,
        shuffle=True,
        num_workers=opt.num_workers,
        pin_memory=True,
    )
    valid_dataloader = torch.utils.data.DataLoader(
        valid_dataset,
        batch_size=opt.batch_size,
        shuffle=False,
        num_workers=opt.num_workers,
        pin_memory=True,
    )
    return train_dataloader, valid_dataloader


if __name__ == "__main__":
    opt = get_option()
    train_dataloader, valid_dataloader = get_dataloader(opt)

    for i, batch in enumerate(train_dataloader):
        print(batch)
