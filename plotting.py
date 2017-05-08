from __future__ import division
import os
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from mpl_toolkits.axes_grid1 import make_axes_locatable
from operator import sub


def make_variable_histograms(sig_df, bkg_df, filename="vars.pdf"):
    """Produce histograms comparing the signal and background distribution
    of availible variables and write them to filename"""

    def plot_histograms(df, ax):
        """Plot histograms for every column in df"""
        return df.hist(bins=100, ax=ax, alpha=0.5, weights=df.EvtWeight,
                       normed=True)

    plt.style.use("ggplot")

    fig_size = (50, 31)

    fig, ax = plt.subplots()
    fig.set_size_inches(fig_size)

    ax = plot_histograms(sig_df, ax).flatten()[:len(sig_df.columns)]
    plot_histograms(bkg_df, ax)

    fig.savefig(filename)


def make_corelation_plot(df, filename="corr.pdf"):
    """Produce 2D histogram representing the correlation matrix of dataframe
    df. Written to filename."""

    plt.style.use("ggplot")

    corr = df.corr()
    nvars = len(corr.columns)

    fig, ax = plt.subplots()
    ms = ax.matshow(corr, vmin=-1, vmax=1)

    fig.set_size_inches(1 + nvars / 1.5, 1 + nvars / 1.5)
    plt.xticks(xrange(nvars), corr.columns, rotation=90)
    plt.yticks(xrange(nvars), corr.columns)
    ax.tick_params(axis='both', which='both', length=0)  # hide ticks
    ax.grid(False)

    # Workaround for using colorbars with tight_layout
    # https://matplotlib.org/users/tight_layout_guide.html#colorbar
    divider = make_axes_locatable(plt.gca())
    cax = divider.append_axes("right", "5%", pad="3%")
    plt.colorbar(ms, cax=cax)

    plt.tight_layout()

    fig.savefig(filename)


def make_response_plot(sig_df_train, sig_df_test, bkg_df_train, bkg_df_test,
                       mva, filename="overtrain.pdf", bins=50):
    """Produce MVA response plot, comparing testing and training samples"""


    plt.style.use("ggplot")

    if hasattr(mva, "decision_function"):
        df = pd.concat((sig_df_train, sig_df_test, bkg_df_train, bkg_df_test))
        MVA_std = df.MVA.std()
        low = df.MVA.quantile(0.01) - MVA_std
        high = df.MVA.quantile(0.99) + MVA_std
        x_range = (low, high)
    else:
        x_range = (0, 1)

    fig, ax = plt.subplots()

    # Plot histograms of test samples
    for df in (sig_df_test, bkg_df_test):
        ax = df.MVA.plot.hist(bins=bins, ax=ax, weights=df.EvtWeight,
                              normed=True, range=x_range, alpha=0.5)

    plt.gca().set_prop_cycle(None)  # use the same colours again

    # Plot error bar plots of training samples
    for df in (sig_df_train, bkg_df_train):
        hist, bin_edges = np.histogram(df.MVA, bins=bins, range=x_range,
                                       weights=df.EvtWeight, density=True)
        scale = len(sig_df_test.index) / hist.sum()
        yerr = np.sqrt(hist * scale) / scale
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

        ax.errorbar(bin_centers, hist, fmt=",",
                    yerr=yerr, xerr=(-sub(*x_range) / bins / 2))

    fig.savefig(filename)
