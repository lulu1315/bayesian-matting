import numpy as np
from scipy.ndimage.filters import gaussian_filter
import cv2

from orchard_bouman_clust import clustFunc


def matlab_style_gauss2d(shape=(3, 3), sigma=0.5):
    """
    2D gaussian mask - should give the same result as MATLAB's
    fspecial('gaussian',[shape],[sigma])
    """
    m, n = [(ss-1.)/2. for ss in shape]
    y, x = np.ogrid[-m:m+1, -n:n+1]
    h = np.exp(-(x*x + y*y)/(2.*sigma*sigma))
    h[h < np.finfo(h.dtype).eps*h.max()] = 0
    sumh = h.sum()
    if sumh != 0:
        h /= sumh
    return h


# returns the surrounding N-rectangular neighborhood of matrix m, centered
# at pixel (x,y), (odd valued N)
def get_window(m, x, y, N):
    h, w, c = m.shape
    halfN = N//2
    r = np.zeros((N, N, c))
    xmin = max(0, x - halfN); xmax = min(w, x + (halfN+1))
    ymin = max(0, y - halfN); ymax = min(h, y + (halfN+1))
    pxmin = halfN - (x-xmin); pxmax = halfN + (xmax-x)
    pymin = halfN - (y-ymin); pymax = halfN + (ymax-y)

    r[pymin:pymax, pxmin:pxmax, :] = m[ymin:ymax, xmin:xmax, :]
    return r


def solve(mu_F, Sigma_F, mu_B, Sigma_B, C, sigma_C, alpha_init, maxIter, minLike):
    '''
    Solves for F,B and alpha that maximize the sum of log
    likelihoods at the given pixel C.
    input:
    mu_F - means of foreground clusters (for RGB, of size 3x#Fclusters)
    Sigma_F - covariances of foreground clusters (for RGB, of size
    3x3x#Fclusters)
    mu_B,Sigma_B - same for background clusters
    C - observed pixel
    alpha_init - initial value for alpha
    maxIter - maximal number of iterations
    minLike - minimal change in likelihood between consecutive iterations

    returns:
    F,B,alpha - estimate of foreground, background and alpha
    channel (for RGB, each of size 3x1)
    '''
    I = np.eye(3)
    vals = []
    FMax = 0
    BMax = 0
    alphaMax = 0
    maxlike = - np.inf
    invsgma2 = 1/sigma_C**2
    for i in range(mu_F.shape[0]):
        mu_Fi = mu_F[i, :]
        invSigma_Fi = np.linalg.inv(Sigma_F[i, :, :])
        for j in range(mu_B.shape[0]):
            mubi = mu_B[j, :]
            invSigma_Bi = np.linalg.inv(Sigma_B[j, :, :])

            alpha = alpha_init
            myiter = 1
            lastLike = -1.7977e+308
            while True:
                # solve for F,B
                A11 = invSigma_Fi + I*alpha**2 * invsgma2
                A12 = I*alpha*(1-alpha) * invsgma2
                A22 = invSigma_Bi+I*(1-alpha)**2 * invsgma2
                A = np.vstack((np.hstack((A11, A12)), np.hstack((A12, A22))))
                b = np.array([invSigma_Fi @ mu_Fi + C*(alpha) * invsgma2, invSigma_Bi @ mubi + C*(1-alpha) * invsgma2])
                b = b.reshape(6, 1)

                X = np.linalg.solve(A, b)
                F = np.maximum(0, np.minimum(1, X[0:3]))
                B = np.maximum(0, np.minimum(1, X[3:6]))
                # solve for alpha
                alpha = np.maximum(0, np.minimum(1, ((C[:, np.newaxis]-B).T @ (F-B))/np.sum((F-B)**2)))
                # calculate likelihood
                L_C = - np.sum((C[:, np.newaxis]-alpha*F-(1-alpha)*B)**2) * invsgma2
                L_F = - ((F-mu_Fi[:, np.newaxis]).T @ invSigma_Fi @ (F-mu_Fi[:, np.newaxis]))/2
                L_B = - ((B-mubi[:, np.newaxis]).T @ invSigma_Bi @ (B-mubi[:, np.newaxis]))/2
                like = (L_C + L_F + L_B)[0, 0]

                if like > maxlike:
                    alphaMax = alpha
                    maxLike = like
                    FMax = F
                    BMax = B

                if myiter >= maxIter or abs(like-lastLike) <= minLike:
                    break

                lastLike = like
                myiter += 1
        return FMax, BMax, alphaMax


def bayesian_matte(img, trimap, sigma=8, N=25, minN=10):
    img = img/255

    h, w, c = img.shape
    alpha = np.zeros((h, w))

    fg_mask = trimap == 255
    bg_mask = trimap == 0
    unknown_mask = True ^ np.logical_or(fg_mask, bg_mask)
    foreground = img*np.repeat(fg_mask[:, :, np.newaxis], 3, axis=2)
    background = img*np.repeat(bg_mask[:, :, np.newaxis], 3, axis=2)

    gaussian_weights = matlab_style_gauss2d((N, N), sigma)
    gaussian_weights = gaussian_weights/np.max(gaussian_weights)

    alpha[fg_mask] = 1
    F = np.zeros(img.shape)
    B = np.zeros(img.shape)
    alphaRes = np.zeros(trimap.shape)

    n = 1
    alpha[unknown_mask] = np.nan
    nUnknown = np.sum(unknown_mask)
    unkreg = unknown_mask

    kernel = np.ones((3, 3))
    while n < nUnknown:
        unkreg = cv2.erode(unkreg.astype(np.uint8), kernel, iterations=1)
        unkpixels = np.logical_and(np.logical_not(unkreg), unknown_mask)

        Y, X = np.nonzero(unkpixels)

        for i in range(Y.shape[0]):
            if n % 100 == 0:
                print(n, nUnknown)
            y, x = Y[i], X[i]
            p = img[y, x]
            # Try cluster Fg, Bg in p's known neighborhood

            # take surrounding alpha values
            a = get_window(alpha[:, :, np.newaxis], x, y, N)[:, :, 0]

            # Take surrounding foreground pixels
            f_pixels = get_window(foreground, x, y, N)
            f_weights = (a**2 * gaussian_weights).ravel()

            f_pixels = np.reshape(f_pixels, (N*N, 3))
            posInds = f_weights > 0
            f_pixels = f_pixels[posInds, :]
            f_weights = f_weights[posInds]

            # Take surrounding foreground pixels
            b_pixels = get_window(background, x, y, N)
            b_weights = ((1-a)**2 * gaussian_weights).ravel()

            b_pixels = np.reshape(b_pixels, (N*N, 3))
            posInds = b_weights > 0
            b_pixels = b_pixels[posInds, :]
            b_weights = b_weights[posInds]

            # if not enough data, return to it later...
            if len(f_weights) < minN or len(b_weights) < minN:
                continue
            # Partition foreground and background pixels to clusters (in a weighted manner)
            mu_f, sigma_f = clustFunc(f_pixels, f_weights)
            mu_b, sigma_b = clustFunc(b_pixels, b_weights)

            alpha_init = np.nanmean(a.ravel())
            # Solve for F,B for all cluster pairs
            f, b, alphaT = solve(mu_f, sigma_f, mu_b, sigma_b, p, 0.01, alpha_init, 50, 1e-6)
            foreground[y, x] = f.ravel()
            background[y, x] = b.ravel()
            alpha[y, x] = alphaT
            unknown_mask[y, x] = 0
            n += 1

    return alpha


def main():
    img = scipy.misc.imread("gandalf.png")[:, :, :3]
    trimap = scipy.misc.imread("gandalfTrimap.png", flatten='True')
    alpha = bayesian_matte(img, trimap)
    #scipy.misc.imsave('gandalfAlpha.png', alpha)
    plt.title("Alpha matte")
    plt.imshow(alpha, cmap='gray')
    plt.show()

if __name__ == '__main__':
    import matplotlib.pyplot as plt
    import scipy.misc
    main()



